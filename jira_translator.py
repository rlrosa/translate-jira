import os
import re
import argparse
import sys
import copy
import json
import traceback
import warnings
from dotenv import load_dotenv

# Load environment variables from .env file before anything else
load_dotenv()

from datetime import datetime, timedelta, timezone
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
__version__ = "4.1.0"
# Your Jira server domain
JIRA_DOMAIN = os.getenv("JIRA_DOMAIN", "example.atlassian.net")
# Identifier to mark and timestamp translations
TRANSLATION_ID_PREFIX_BASE = "TR4NSL4T3D-v"
TRANSLATION_ID_PREFIX = f"{TRANSLATION_ID_PREFIX_BASE}{__version__}"
# The title for the expand macro in Jira
EXPAND_TITLE = "Translation / 翻译"
OLD_EXPAND_TITLE = "Translation" # For backwards compatibility
# The time difference to trigger a re-translation
RETRANSLATE_THRESHOLD = timedelta(seconds=60)
# How far back to look for updates
LOOKBACK_TIMEDELTA = timedelta(days=1)
# Gemini model for cloud translation
GEMINI_MODEL = "gemini-2.5-flash-lite"
# Lock file to prevent concurrent runs
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translator.lock")
# --- Dynamic Custom Fields ---
try:
    with open("config.json", "r") as f:
        config_data = json.load(f)
        CUSTOM_FIELDS = config_data.get("custom_fields", [])
except FileNotFoundError:
    CUSTOM_FIELDS = []
    print("No config.json found. Custom fields will not be translated.")

# --- Default Servers ---
# Default URL for the legacy translation server
DEFAULT_TRANSLATION_SERVER_URL = os.getenv("TRANSLATION_SERVER_URL", "http://127.0.0.1:5000/translate")
# Default Ollama settings (New default endpoint)
DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


# --- Dynamic Imports for Optional Libraries ---
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=FutureWarning)
        import google.generativeai as genai
except ImportError:
    genai = None

class TranslationError(Exception):
    """Custom exception for script-specific errors, linked to a Jira issue."""
    def __init__(self, message, issue_key=None, details=None):
        self.issue_key = issue_key
        self.message = message
        self.details = details # Optional field for more context
        super().__init__(self.message)

    def __str__(self):
        """Provides a clean, formatted error message."""
        prefix = f"[Issue: {self.issue_key}] " if self.issue_key else "[General Error] "
        msg = f"{prefix}{self.message}"
        if self.details:
            msg += f"\n    Details: {self.details}"
        return msg

# --- Helper Functions ---
def is_english(text):
    """A simple heuristic to detect if text is primarily English."""
    if not text:
        return False
    # This is a very basic check. We assume if it has Chinese characters, it's not primarily English.
    if re.search("[\u4e00-\u9fff]", text):
        return False
    return True

def strip_brackets_for_translation(text):
    """Removes content within square [] or corner 【】 brackets for translation."""
    if not isinstance(text, str):
        return ""
    # This regex removes matching pairs of brackets and their content
    return re.sub(r'\[.*?\]|【.*?】', '', text).strip()

# --- Ollama Translation (New Default) ---
def translate_text_ollama(text_list, host, model_name, max_timeout=300):
    """
    Uses an Ollama endpoint to translate text fragments.
    Replicates the logic of processing a numbered list to save round-trips.
    """
    if not text_list: return [], None
    print(f"    -> Translating {len(text_list)} fragments via Ollama ({model_name} @ {host})...")

    # Construct a numbered list prompt
    numbered_list = "\n".join([f"{i+1}. {text}" for i, text in enumerate(text_list)])
    
    # Prompt engineering similar to Gemini but tuned for instruction-following local models
    prompt = (
        "You are a professional technical translator. Your task is to process a numbered list of text fragments.\n"
        "For each fragment:\n"
        "1. Identify if it is English or Chinese.\n"
        "2. Translate it to the OTHER language (English -> Chinese, Chinese -> English).\n"
        "3. If it is neither, keep it unchanged.\n\n"
        "IMPORTANT RULES:\n"
        "- Your response must ONLY be a numbered list corresponding to the input.\n"
        "- Do not add introductions, explanations, or notes.\n"
        "- Preserve the original formatting and punctuation where possible.\n\n"
        "Input List:\n"
        f"{numbered_list}\n\n"
        "Translated List:"
    )

    # Dynamic timeout: base 30s + 0.05s per character in prompt, capped at max_timeout
    calculated_timeout = min(30.0 + (len(prompt) * 0.05), float(max_timeout))

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1, # Low temperature for consistent translation
            "num_predict": 2048 # Allow enough tokens for the response
        }
    }

    try:
        # Use endpoint /api/generate as seen in test_llm.py
        url = f"{host.rstrip('/')}/api/generate"
        start_time = datetime.now()
        response = requests.post(url, json=payload, timeout=calculated_timeout)
        response.raise_for_status()
        
        data = response.json()
        raw_text = data.get("response", "").strip()
        
        duration = (datetime.now() - start_time).total_seconds()
        # print(f"       (Ollama took {duration:.2f}s)")

        # Parse the numbered list response
        # Matches "1. Text" or "1 Text" or "1.Text"
        translated_fragments = re.findall(r"^\d+[\.\)]\s*(.*)", raw_text, re.MULTILINE)

        # Fallback parsing: if model returns just lines without numbers (unlikely with prompt, but possible)
        if len(translated_fragments) == 0 and len(text_list) > 0 and raw_text:
             translated_fragments = [line.strip() for line in raw_text.split('\n') if line.strip()]

        if len(translated_fragments) != len(text_list):
            # Log details for debugging, but raise error to trigger fallback
            details = f"Expected {len(text_list)} items, got {len(translated_fragments)}. Raw output: {raw_text[:200]}..."
            raise TranslationError(f"Ollama translation count mismatch.", details=details)

        return translated_fragments, "MIXED"

    except requests.exceptions.RequestException as e:
        raise TranslationError(f"Could not connect to Ollama server at {host}", details=str(e))
    except Exception as e:
        raise TranslationError("Ollama translation failed.", details=str(e))

# --- Remote Translation (Legacy Server) ---
def translate_text_remote(text_list, server_url):
    """
    Sends a list of texts to the remote translation server (Legacy/Fallback).
    """
    if not text_list:
        return [], None
    print(f"    -> Translating {len(text_list)} fragments via Legacy Server ({server_url})...")
    try:
        response = requests.post(server_url, json={"texts": text_list}, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data.get("translated_texts", []), "MIXED"
    except requests.exceptions.RequestException as e:
        raise TranslationError(f"Could not connect to translation server at {server_url}", details=str(e))

# --- Hybrid Wrapper (Ollama -> Fallback to Remote) ---
def translate_hybrid(text_list, ollama_host, ollama_model, legacy_url, ollama_timeout=300):
    """
    Attempts to use Ollama first. If it fails, falls back to the legacy server.
    """
    try:
        return translate_text_ollama(text_list, ollama_host, ollama_model, ollama_timeout)
    except TranslationError as e:
        print(f"    ⚠️  Ollama Error: {e}")
        print("    ⚠️  Falling back to Legacy Translation Server...")
        return translate_text_remote(text_list, legacy_url)

# --- Gemini Translation & Detection (Cloud) ---
def translate_text_gemini(text_list):
    """
    Uses the Gemini API to translate a list of mixed-language text fragments in a single call.
    """
    if not text_list: return [], None
    print(f"    -> Translating {len(text_list)} individual text fragments via Gemini...")

    numbered_list = "\n".join([f"{i+1}. {text}" for i, text in enumerate(text_list)])

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            "You are an expert translator. Your task is to process a numbered list of text fragments. "
            "For each fragment, you must first identify its primary language (either English or Chinese). "
            "Then, translate it to the OTHER language (English to Chinese, Chinese to English). "
            "If a fragment is not in English or Chinese, return it unchanged. "
            "Your response MUST be a numbered list with the exact same number of fragments as the input. "
            "Each item in your response list should be only the translated (or unchanged) text. "
            "Do not add any other commentary, notes, or explanations.\n\n"
            "Here is the numbered list of fragments to process:\n"
            f"---\n{numbered_list}\n---"
        )
        response = model.generate_content(prompt)
        # Parse the numbered list response from Gemini
        translated_fragments = re.findall(r"^\d+\.\s*(.*)", response.text, re.MULTILINE)

        if len(translated_fragments) != len(text_list):
            raise TranslationError(
                f"Translation count mismatch. Expected {len(text_list)}, got {len(translated_fragments)}.",
                details=f"Gemini Response: {response.text}"
            )
        return translated_fragments, "MIXED"
    except Exception as e:
        raise TranslationError("Gemini translation API call failed.", details=str(e))

# --- ADF (Atlassian Document Format) Helpers ---
def extract_text_from_adf(nodes):
    """Recursively traverses ADF nodes and extracts a list of text strings."""
    texts = []
    def _recursive_extract(content):
        if not content: return
        for node in content:
            if node.get("type") == "text" and node.get("text", "").strip():
                texts.append(node["text"])
            if "content" in node:
                _recursive_extract(node["content"])
    _recursive_extract(nodes)
    return texts

def rebuild_adf_with_translation(original_nodes, translated_texts):
    """Recursively rebuilds the ADF, replacing text with translated versions."""
    translated_iter = iter(translated_texts)
    new_nodes = copy.deepcopy(original_nodes)
    def _recursive_rebuild(content):
        if not content: return
        for node in content:
            if node.get("type") == "text" and node.get("text", "").strip():
                try: node["text"] = next(translated_iter)
                except StopIteration: print("    -> WARNING: Ran out of translated text during rebuild.")
            if "content" in node:
                _recursive_rebuild(node["content"])
    _recursive_rebuild(new_nodes)
    return new_nodes

# --- Jira API Functions ---
def get_jira_issues(session, ticket_id=None, projects=None):
    if ticket_id:
        print(f"Fetching specific issue: {ticket_id}...")
        jql = f"issueKey = '{ticket_id}'"
    else:
        lookback_days = LOOKBACK_TIMEDELTA.days
        jql_parts = ['labels = translate', f'updated >= -{lookback_days}d']
        if projects:
            project_keys = [key.strip().upper() for key in projects.split(',')]
            jql_parts.append(f"project in ({','.join(project_keys)})")
            print(f"Searching for recently updated issues in projects: {', '.join(project_keys)}...")
        else:
            print("Searching for recently updated issues with label 'translate' across all projects...")
        jql = ' AND '.join(jql_parts)

    url = f"https://{JIRA_DOMAIN}/rest/api/3/search/jql"
    custom_field_ids = [cf["id"] for cf in CUSTOM_FIELDS]
    base_fields = "summary,comment,description,created,updated"
    fields_to_fetch = f"{base_fields},{','.join(custom_field_ids)}" if custom_field_ids else base_fields
    params = {"jql": jql, "fields": fields_to_fetch, "expand": "changelog"}
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        issues_list = data.get("issues", [])
        print(f"Found {len(issues_list)} issues to process.")
        return issues_list
    except requests.exceptions.RequestException as e:
        raise TranslationError("Failed to fetch Jira issues.", details=str(e))

def get_last_field_update_time(issue, field_id):
    """Finds the most recent changelog entry for a specific field."""
    changelog = issue.get("changelog", {}).get("histories", [])
    for history in changelog:
        for item in history.get("items", []):
            if item.get("fieldId") == field_id:
                return parse_jira_timestamp(history["created"])

    if issue["fields"].get(field_id):
        return parse_jira_timestamp(issue["fields"]["created"])

    return None

def parse_jira_timestamp(timestamp_str):
    """Robustly parses Jira's ISO 8601-like date string into a UTC datetime object."""
    if not timestamp_str:
        return None
    if timestamp_str.endswith("Z"):
        timestamp_str = timestamp_str.replace("Z", "+00:00")
    elif timestamp_str[-5] in ('+', '-') and timestamp_str[-3] != ':':
        timestamp_str = timestamp_str[:-2] + ':' + timestamp_str[-2:]
    return datetime.fromisoformat(timestamp_str).astimezone(timezone.utc)

def process_field(field_name, adf_nodes, updated_at, verbose=False, re_run_old_translations=False):
    if not adf_nodes or not updated_at:
        return False, None, None

    VALID_EXPAND_TITLES = [EXPAND_TITLE, OLD_EXPAND_TITLE]
    original_adf_nodes = []
    translation_node = None

    for node in adf_nodes:
        if node.get("type") == "expand":
            title = node.get("attrs", {}).get("title", "").strip()
            if title in VALID_EXPAND_TITLES:
                if not translation_node:
                    translation_node = node
                continue
        original_adf_nodes.append(node)

    nodes_to_translate = [node for node in original_adf_nodes if node.get("type") != "expand"]
    texts_in_original = extract_text_from_adf(nodes_to_translate)

    if not texts_in_original:
        return False, None, None

    if verbose:
        print(f"    -> [VERBOSE] {field_name} Last Updated (UTC): {updated_at}")

    # Case 1: No translation block exists yet
    if not translation_node:
        print(f"  -> {field_name} needs initial translation.")
        return True, original_adf_nodes, nodes_to_translate

    # Case 2: A translation block exists
    translation_content_text = "\n".join(extract_text_from_adf(translation_node.get("content", [])))
    matches = list(re.finditer(f"{TRANSLATION_ID_PREFIX_BASE}([\\d\\.]+)-(\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}})UTC", translation_content_text))

    if not matches:
        print(f"  -> {field_name} has a translation block but no signature. Re-translating.")
        return True, original_adf_nodes, nodes_to_translate

    # Get the *last* signature parsed (handling cloned tickets with old stale signatures at the top)
    last_match = matches[-1]
    found_version, translation_timestamp_str = last_match.groups()
    translation_time = datetime.strptime(translation_timestamp_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)

    if verbose:
        print(f"    -> [VERBOSE] Found signature version: {found_version}")
        print(f"    -> [VERBOSE] Last Translation Time (UTC):  {translation_time}")

    if updated_at - translation_time >= RETRANSLATE_THRESHOLD:
        print(f"  -> {field_name} content is outdated. Re-translating...")
        return True, original_adf_nodes, nodes_to_translate

    if re_run_old_translations:
        current_version_tuple = tuple(map(int, __version__.split('.')))
        found_version_tuple = tuple(map(int, found_version.split('.')))
        if found_version_tuple < current_version_tuple:
            print(f"  -> {field_name} has old translation version ({found_version}). Re-translating...")
            return True, original_adf_nodes, nodes_to_translate

    print(f"  -> {field_name} is already translated and up-to-date.")
    return False, None, None

def update_jira_issue_fields(session, issue_key, adf_updates, verbose=False):
    url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}"
    payload = {"fields": adf_updates}
    try:
        response = session.put(url, json=payload)
        response.raise_for_status()
        print(f"    -> ✅ Successfully updated fields in issue {issue_key}.")
    except requests.exceptions.HTTPError as e:
        error_text = e.response.text if e.response is not None else str(e)
        if verbose:
            print("    -> Sent payload:")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise TranslationError("Failed to update issue fields.", issue_key=issue_key, details=error_text)

def update_jira_comment(session, issue_key, comment_id, original_nodes, translated_nodes, verbose=False):
    url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}/comment/{comment_id}"
    now_utc = datetime.now(timezone.utc)
    translation_id = f"{TRANSLATION_ID_PREFIX}-{now_utc.strftime('%Y-%m-%dT%H:%M:%S')}UTC"
    final_content_nodes = original_nodes + [{"type": "expand", "attrs": {"title": EXPAND_TITLE}, "content": translated_nodes + [{"type": "paragraph", "content": [{"type": "text", "text": f"--- {translation_id} ---"}]}]}]
    adf_payload = {"body": {"type": "doc", "version": 1, "content": final_content_nodes}}
    try:
        response = session.put(url, json=adf_payload)
        response.raise_for_status()
        print(f"    -> ✅ Successfully updated comment {comment_id} in issue {issue_key}.")
    except requests.exceptions.HTTPError as e:
        error_text = e.response.text if e.response is not None else str(e)
        if verbose:
            print("    -> Sent payload:")
            print(json.dumps(adf_payload, indent=2, ensure_ascii=False))
        raise TranslationError(f"Failed to update comment {comment_id}.", issue_key=issue_key, details=error_text)

def process_single_issue(issue, args, translate_func, session):
    issue_key = issue["key"]
    print(f"\nProcessing issue: {issue_key}")
    errors_for_this_issue = []

    try:
        if args.full_ticket:
            process_and_update_issue_fields(issue, args, translate_func, session)
    except TranslationError as e:
        e.issue_key = e.issue_key or issue_key
        errors_for_this_issue.append(e)
    except Exception as e:
        errors_for_this_issue.append(TranslationError(
            f"An unexpected error occurred while processing fields.",
            issue_key=issue_key,
            details=traceback.format_exc()
        ))

    comments = issue.get("fields", {}).get("comment", {}).get("comments", [])
    for comment in comments:
        try:
            comment_updated_at = parse_jira_timestamp(comment["updated"])
            needs_translation, original_nodes, nodes_to_translate = process_field(f"Comment {comment['id']}", comment.get("body", {}).get("content", []), comment_updated_at, args.verbose, args.re_run_old_translations)
            if needs_translation:
                texts_to_translate = extract_text_from_adf(nodes_to_translate)
                if not texts_to_translate:
                    print(f"  -> Skipping comment {comment['id']}: No text content found.")
                    continue

                translated_texts, _ = translate_func(texts_to_translate)
                if translated_texts:
                    translated_nodes = rebuild_adf_with_translation(nodes_to_translate, translated_texts)
                    print("\n" + "="*80)
                    print(f"PROPOSED CHANGE FOR COMMENT {comment['id']} in {issue_key}")
                    print("-"*80)
                    print("ORIGINAL TEXT:\n" + "\n".join(texts_to_translate))
                    print("-"*80)
                    print("PROPOSED TRANSLATED TEXT:\n" + "\n".join(translated_texts))
                    print("="*80)
                    apply_change = False
                    if args.no_confirm:
                        print("Auto-confirming change (--no-confirm specified).")
                        apply_change = True
                    else:
                        choice = input("Apply this change? (y/n) - [q to quit]: ").lower()
                        if choice == 'y': apply_change = True
                        elif choice == 'q': print("Quitting script."); sys.exit(0)
                        else: print("Skipping update.")
                    if apply_change:
                        update_jira_comment(session, issue_key, comment['id'], original_nodes, translated_nodes, verbose=args.verbose)

        except TranslationError as e:
            e.issue_key = e.issue_key or issue_key
            errors_for_this_issue.append(e)
        except Exception as e:
            errors_for_this_issue.append(TranslationError(
                f"An unexpected error occurred while processing comment {comment.get('id', 'N/A')}.",
                issue_key=issue_key,
                details=traceback.format_exc()
            ))

    return errors_for_this_issue

def process_and_update_issue_fields(issue, args, translate_func, session):
    issue_key = issue["key"]
    proposed_field_changes = []

    summary = issue["fields"].get("summary")
    if summary and is_english(summary) and " / " not in summary:
        print("  -> Summary needs translation.")
        summary_to_translate = strip_brackets_for_translation(summary)
        if summary_to_translate:
            translated_texts, _ = translate_func([summary_to_translate])
            if translated_texts and translated_texts[0]:
                translated_summary = translated_texts[0].strip()
                if translated_summary and translated_summary != summary_to_translate:
                    new_summary = f"{summary} / {translated_summary}"
                    proposed_field_changes.append({
                        "name": "Summary",
                        "field_id": "summary",
                        "original_texts": [summary],
                        "translated_texts": [translated_summary],
                        "final_adf_content": new_summary
                    })

    desc_adf = issue["fields"].get("description")
    desc_update_time = get_last_field_update_time(issue, "description")
    if desc_adf and desc_update_time:
        needs_translation, original_nodes, nodes_to_translate = process_field("Description", desc_adf.get("content"), desc_update_time, args.verbose, args.re_run_old_translations)
        if needs_translation:
            texts_to_translate = extract_text_from_adf(nodes_to_translate)
            if texts_to_translate:
                translated_texts, _ = translate_func(texts_to_translate)
                if translated_texts:
                    translated_nodes = rebuild_adf_with_translation(nodes_to_translate, translated_texts)
                    now_utc = datetime.now(timezone.utc)
                    translation_id = f"{TRANSLATION_ID_PREFIX}-{now_utc.strftime('%Y-%m-%dT%H:%M:%S')}UTC"
                    final_nodes = original_nodes + [{"type": "expand", "attrs": {"title": EXPAND_TITLE}, "content": translated_nodes + [{"type": "paragraph", "content": [{"type": "text", "text": f"--- {translation_id} ---"}]}]}]
                    proposed_field_changes.append({"name": "Description", "field_id": "description", "original_texts": texts_to_translate, "translated_texts": translated_texts, "final_adf_content": final_nodes})

    for custom_field in CUSTOM_FIELDS:
        cf_id = custom_field["id"]
        cf_name = custom_field["name"]
        
        cf_adf = issue["fields"].get(cf_id)
        cf_update_time = get_last_field_update_time(issue, cf_id)
        if cf_adf and cf_update_time:
            needs_translation, original_nodes, nodes_to_translate = process_field(f"{cf_name} ({cf_id})", cf_adf.get("content"), cf_update_time, args.verbose, args.re_run_old_translations)
            if needs_translation:
                texts_to_translate = extract_text_from_adf(nodes_to_translate)
                if texts_to_translate:
                    translated_texts, _ = translate_func(texts_to_translate)
                    if translated_texts:
                        translated_nodes = rebuild_adf_with_translation(nodes_to_translate, translated_texts)
                        now_utc = datetime.now(timezone.utc)
                        translation_id = f"{TRANSLATION_ID_PREFIX}-{now_utc.strftime('%Y-%m-%dT%H:%M:%S')}UTC"
                        final_nodes = original_nodes + [{"type": "expand", "attrs": {"title": EXPAND_TITLE}, "content": translated_nodes + [{"type": "paragraph", "content": [{"type": "text", "text": f"--- {translation_id} ---"}]}]}]
                        proposed_field_changes.append({"name": f"{cf_name} ({cf_id})", "field_id": cf_id, "original_texts": texts_to_translate, "translated_texts": translated_texts, "final_adf_content": final_nodes})

    if proposed_field_changes:
        print("\n" + "="*80)
        print(f"PROPOSED FIELD CHANGES FOR ISSUE {issue_key}")
        for change in proposed_field_changes:
            print("-" * 80)
            print(f"FIELD: {change['name']}")
            print("ORIGINAL TEXT:\n" + "\n".join(change['original_texts']))
            print("-" * 40)
            print("PROPOSED TRANSLATED TEXT:\n" + "\n".join(change['translated_texts']))
        print("="*80)

        apply_change = False
        if args.no_confirm:
            print("Auto-confirming field changes (--no-confirm specified).")
            apply_change = True
        else:
            choice = input("Apply these field changes? (y/n) - [q to quit]: ").lower()
            if choice == 'y': apply_change = True
            elif choice == 'q': print("Quitting script."); sys.exit(0)
            else: print("Skipping field updates for this issue.")

        if apply_change:
            updates_payload = {}
            for change in proposed_field_changes:
                if change['field_id'] == 'summary':
                    updates_payload[change['field_id']] = change['final_adf_content']
                else:
                    updates_payload[change['field_id']] = {"type": "doc", "version": 1, "content": change['final_adf_content']}

            if updates_payload:
                update_jira_issue_fields(session, issue_key, updates_payload, verbose=args.verbose)

def main():
    if os.path.exists(LOCK_FILE):
        print(f"[{datetime.now()}] Script is already running. Lock file found: {LOCK_FILE}. Exiting.")
        sys.exit(1)

    all_errors = []
    try:
        with open(LOCK_FILE, "w") as f: f.write(str(os.getpid()))

        jira_email_env = os.getenv("JIRA_EMAIL")
        parser = argparse.ArgumentParser(description="Translate Jira comments, descriptions, and custom fields.")
        parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
        parser.add_argument("--email", help="Your Jira email address. Can also be set via JIRA_EMAIL env var.", required=not jira_email_env)
        parser.add_argument("--ticket_id", help="Optional: A specific Jira ticket ID to process.")
        parser.add_argument("--projects", help="Optional: Comma-separated list of Jira project keys to search within.")
        parser.add_argument("--full-ticket", action="store_true", help="Enable translation of the Summary, Description, and Status Notes fields.")
        parser.add_argument("--no-confirm", action="store_true", help="Skip confirmation and auto-apply all changes.")
        
        # Translation Provider Options
        translation_group = parser.add_mutually_exclusive_group()
        translation_group.add_argument("--use-gemini", action="store_true", help="Use the cloud-based Gemini API for translation.")
        translation_group.add_argument("--use-remote-only", action="store_true", help="Force usage of the legacy remote server only (disable Ollama).")
        translation_group.add_argument("--use-remote", action="store_true", help="Deprecated alias for --use-remote-only")

        # Configuration Arguments
        parser.add_argument("--server-url", default=DEFAULT_TRANSLATION_SERVER_URL, help=f"Legacy translation server URL. Default: {DEFAULT_TRANSLATION_SERVER_URL}")
        parser.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST, help=f"Ollama server URL. Default: {DEFAULT_OLLAMA_HOST}")
        parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL, help=f"Ollama model name. Default: {DEFAULT_OLLAMA_MODEL}")
        parser.add_argument("--ollama-timeout", type=int, default=300, help="Max timeout in seconds for Ollama translations. Default: 300")
        
        
        parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers to process tickets.")
        parser.add_argument("--verbose", action="store_true", help="Enable detailed logging.")
        parser.add_argument("--re-run-old-translations", action="store_true", help="Force re-translation for older script versions.")
        args = parser.parse_args()

        translate_func = None
        if args.use_gemini:
            print("Using GEMINI (cloud) for translation.")
            if not genai:
                raise TranslationError("'google-generativeai' is required. Install with: pip install google-generativeai")
            gemini_api_key = os.getenv("GEMINI_API_KEY")
            if not gemini_api_key:
                raise TranslationError("GEMINI_API_KEY environment variable not set.")
            genai.configure(api_key=gemini_api_key)
            translate_func = translate_text_gemini
        
        elif args.use_remote or args.use_remote_only:
            print(f"Using LEGACY REMOTE server only: {args.server_url}")
            translate_func = lambda texts: translate_text_remote(texts, args.server_url)
        
        else:
            # Default: Hybrid (Ollama -> Fallback to Remote)
            print(f"Using HYBRID translation mode:")
            print(f"  1. Primary: Ollama ({args.ollama_model} @ {args.ollama_host})")
            print(f"  2. Fallback: Legacy Server ({args.server_url})")
            translate_func = lambda texts: translate_hybrid(texts, args.ollama_host, args.ollama_model, args.server_url, args.ollama_timeout)

        user_email = args.email or jira_email_env
        jira_api_token = os.getenv("JIRA_API_TOKEN")
        if not jira_api_token:
            raise TranslationError("JIRA_API_TOKEN environment variable not set.")

        session = requests.Session()
        session.auth = (user_email, jira_api_token)
        session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

        # Verify authentication before fetching issues since search queries often fail silently (return 0 issues) on bad auth
        auth_check_url = f"https://{JIRA_DOMAIN}/rest/api/3/myself"
        try:
            auth_response = session.get(auth_check_url, timeout=15)
            auth_response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise TranslationError(f"Jira Authentication Failed! Please verify JIRA_EMAIL and JIRA_API_TOKEN in your .env", details=str(e))

        issues = get_jira_issues(session, args.ticket_id, args.projects)
        if not issues:
            print("No issues found to process.")
            if os.path.exists(LOCK_FILE): os.remove(LOCK_FILE)
            return

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_issue = {executor.submit(process_single_issue, issue, args, translate_func, session): issue for issue in issues}
            for future in as_completed(future_to_issue):
                issue = future_to_issue[future]
                try:
                    errors_from_worker = future.result()
                    if errors_from_worker:
                        all_errors.extend(errors_from_worker)
                    else:
                        print(f"-> Finished processing {issue.get('key', 'N/A')} successfully.")
                except Exception:
                    all_errors.append(TranslationError(
                        "A critical exception occurred in the processing thread.",
                        issue_key=issue.get('key', 'N/A'),
                        details=traceback.format_exc()
                    ))

    except TranslationError as e:
        all_errors.append(e)
    except Exception:
        all_errors.append(TranslationError("An unexpected script-level error occurred.", details=traceback.format_exc()))
    finally:
        if all_errors:
            print("\n" + "="*40 + " SCRIPT FINISHED WITH ERRORS " + "="*40)
            print(f"A total of {len(all_errors)} error(s) occurred:")
            for i, error in enumerate(all_errors, 1):
                print(f"\n--- Error {i} ---")
                print(error)
            print("="*105)
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
            sys.exit(1)
        else:
            print(f"\nScript finished successfully. jira_translator v{__version__}")

        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)

if __name__ == "__main__":
    main()
