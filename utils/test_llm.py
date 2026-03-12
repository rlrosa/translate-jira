import requests
import json
import time
import os
import sys
import argparse

# Defaults based on your setup (can be overridden by CLI args)
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
DEFAULT_PROMPT = "What is 2 + 2? Answer with a single number."

def test_connection(host, model, prompt_text):
    print(f"--- 🔍 Diagnostic: Ollama Connection ---")
    print(f"Target Host:  {host}")
    print(f"Target Model: {model}")
    
    # TEST 1: Basic Connectivity
    print("\n1️⃣  Testing Server Reachability...")
    try:
        start = time.time()
        # Ollama root usually returns "Ollama is running"
        res = requests.get(host, timeout=3)
        print(f"   ✅ Server is UP (Status: {res.status_code}, Time: {time.time() - start:.3f}s)")
        print(f"   ℹ️  Response: {res.text.strip()}")
    except Exception as e:
        print(f"   ❌ CRITICAL: Cannot reach server at {host}")
        print(f"      Error: {e}")
        return # Stop if we can't even reach the IP

    # TEST 2: Model Availability
    print("\n2️⃣  Checking Model List...")
    try:
        start = time.time()
        res = requests.get(f"{host}/api/tags", timeout=5)
        if res.status_code == 200:
            data = res.json()
            models = [m['name'] for m in data.get('models', [])]
            print(f"   ✅ API Responded (Time: {time.time() - start:.3f}s)")
            
            if model in models:
                print(f"   ✅ Model '{model}' is installed/available.")
            else:
                # Fuzzy match check (e.g. ignoring :latest tag)
                print(f"   ⚠️  Exact match for '{model}' not found.")
                print(f"       Available models: {', '.join(models)}")
        else:
            print(f"   ❌ API Error: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"   ❌ Failed to fetch models: {e}")

    # TEST 3: Generation (The Timeout Check)
    print(f"\n3️⃣  Testing Generation...")
    print(f"   ❓ Prompt: '{prompt_text}'")
    
    payload = {
        "model": model,
        "prompt": prompt_text,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 100  # Increased to allow for slightly longer custom answers
        }
    }
    
    try:
        print(f"   ⏳ Sending request (Timeout set to 60s)...")
        start = time.time()
        res = requests.post(f"{host}/api/generate", json=payload, timeout=60)
        duration = time.time() - start
        
        if res.status_code == 200:
            ans = res.json().get('response', '').strip()
            print(f"   ✅ SUCCESS! Server replied in {duration:.2f} seconds.")
            print(f"   📝 Output: '{ans}'")
            
            if duration > 10:
                print("\n   ⚠️  NOTE: Response took > 10s.")
                print("       If this was the first request, the model likely had to load into VRAM.")
                print("       Try running this script again immediately; it should be much faster.")
        else:
            print(f"   ❌ Server Error {res.status_code}: {res.text}")
            
    except requests.exceptions.ReadTimeout:
        print(f"   ❌ TIMEOUT: Server took longer than 60s to reply.")
        print("       Possibilities:")
        print("       1. Model is massive (70b) and GPU offloading is slow/failed.")
        print("       2. Server is stuck processing a previous request.")
        print("       3. Using CPU instead of GPU.")
    except Exception as e:
        print(f"   ❌ Request Failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Ollama Connectivity and Inference Speed")
    
    parser.add_argument(
        "--host", 
        default=DEFAULT_HOST, 
        help=f"Ollama Server URL (default: {DEFAULT_HOST})"
    )
    
    parser.add_argument(
        "--model", 
        default=DEFAULT_MODEL, 
        help=f"Model name to test (default: {DEFAULT_MODEL})"
    )

    parser.add_argument(
        "--prompt", 
        default=DEFAULT_PROMPT, 
        help=f"Custom prompt text (default: '{DEFAULT_PROMPT}')"
    )

    args = parser.parse_args()
    
    test_connection(args.host, args.model, args.prompt)
