import logging
import os
import torch
from flask import Flask, request, jsonify
from transformers import MarianMTModel, MarianTokenizer
import time # Import the time module

app = Flask(__name__)

models = {}

def initialize_local_translators():
    """
    Loads the Helsinki-NLP models, assigning each worker process to a specific GPU.
    """
    global models
    if not torch.cuda.is_available():
        app.logger.error("CUDA is not available. Aborting.")
        return

    num_gpus = torch.cuda.device_count()
    worker_pid = os.getpid()
    # Assign a GPU to this worker process
    gpu_id = worker_pid % num_gpus
    device = f"cuda:{gpu_id}"

    app.logger.info(f"Worker PID {worker_pid} initializing on device: {device.upper()}")
    
    try:
        EN_ZH_MODEL_NAME = "Helsinki-NLP/opus-mt-en-zh"
        app.logger.info(f"PID {worker_pid}: Loading {EN_ZH_MODEL_NAME}...")
        en_zh_tokenizer = MarianTokenizer.from_pretrained(EN_ZH_MODEL_NAME)
        en_zh_model = MarianMTModel.from_pretrained(EN_ZH_MODEL_NAME, use_safetensors=True).to(device)
        app.logger.info(f"PID {worker_pid}: Finished loading {EN_ZH_MODEL_NAME}.")
        
        ZH_EN_MODEL_NAME = "Helsinki-NLP/opus-mt-zh-en"
        app.logger.info(f"PID {worker_pid}: Loading {ZH_EN_MODEL_NAME}...")
        zh_en_tokenizer = MarianTokenizer.from_pretrained(ZH_EN_MODEL_NAME)
        zh_en_model = MarianMTModel.from_pretrained(ZH_EN_MODEL_NAME, use_safetensors=True).to(device)
        app.logger.info(f"PID {worker_pid}: Finished loading {ZH_EN_MODEL_NAME}.")

        models = {
            "en_zh": (en_zh_model, en_zh_tokenizer),
            "zh_en": (zh_en_model, zh_en_tokenizer),
            "device": device
        }
    except Exception as e:
        app.logger.error(f"PID {worker_pid}: ERROR - Could not load models. Details: {e}")


def translate_large_text(text, model, tokenizer, device, max_length=500):
    """
    Translates a single large text by splitting it into smaller chunks.
    """
    # Simple split by sentences. Can be improved with more robust sentence tokenizers.
    chunks = text.split('. ')
    translated_text = ""
    
    for chunk in chunks:
        if not chunk.strip():
            continue
        tokenized_chunk = tokenizer(chunk, return_tensors="pt", padding=True, truncation=True, max_length=max_length).to(device)
        translated_tokens = model.generate(**tokenized_chunk)
        translated_chunk = tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0]
        translated_text += translated_chunk + ". "
        
    return translated_text.strip()


@app.route('/translate', methods=['POST'])
def translate():
    req_start_time = time.time()
    worker_pid = os.getpid()
    
    if not models:
        return jsonify({"error": f"Models not loaded in worker PID {worker_pid}"}), 500
        
    data = request.get_json()
    if not data or "texts" not in data:
        return jsonify({"error": "Missing 'texts' in request body"}), 400

    text_list = data["texts"]
    app.logger.info(f"PID {worker_pid}: Received request to translate {len(text_list)} fragments.")

    device = models["device"]
    en_model, en_tokenizer = models["en_zh"]
    zh_model, zh_tokenizer = models["zh_en"]
    
    translated_results = {}
    
    # Batch EN -> ZH translations
    en_indices_to_translate = [i for i, text in enumerate(text_list) if is_english(text)]
    if en_indices_to_translate:
        en_texts = [text_list[i] for i in en_indices_to_translate]
        
        batch_translation_start = time.time()
        
        translated_batch = []
        for text in en_texts:
            # Check if text is too long (heuristic)
            if len(text) > 512:
                translated_batch.append(translate_large_text(text, en_model, en_tokenizer, device))
            else:
                 tokenized_text = en_tokenizer(text, return_tensors="pt", padding=True).to(device)
                 translated_tokens = en_model.generate(**tokenized_text)
                 translated_batch.append(en_tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0])

        for i, original_index in enumerate(en_indices_to_translate):
            translated_results[original_index] = translated_batch[i]
            
        batch_translation_end = time.time()
        app.logger.info(f"PID {worker_pid}: Translated {len(en_texts)} EN->ZH fragments in {batch_translation_end - batch_translation_start:.2f}s")


    # Batch ZH -> EN translations
    zh_indices_to_translate = [i for i, text in enumerate(text_list) if not is_english(text)]
    if zh_indices_to_translate:
        zh_texts = [text_list[i] for i in zh_indices_to_translate]
        
        batch_translation_start = time.time()

        translated_batch = []
        for text in zh_texts:
             if len(text) > 512:
                translated_batch.append(translate_large_text(text, zh_model, zh_tokenizer, device))
             else:
                tokenized_text = zh_tokenizer(text, return_tensors="pt", padding=True).to(device)
                translated_tokens = zh_model.generate(**tokenized_text)
                translated_batch.append(zh_tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0])

        for i, original_index in enumerate(zh_indices_to_translate):
            translated_results[original_index] = translated_batch[i]
            
        batch_translation_end = time.time()
        app.logger.info(f"PID {worker_pid}: Translated {len(zh_texts)} ZH->EN fragments in {batch_translation_end - batch_translation_start:.2f}s")
            
    final_list = [translated_results.get(i, text_list[i]) for i in range(len(text_list))]
    
    req_end_time = time.time()
    app.logger.info(f"PID {worker_pid}: Total request processing time: {req_end_time - req_start_time:.2f}s")

    return jsonify({"translated_texts": final_list})

@app.route('/health', methods=['GET'])
def health_check():
    """A simple health check endpoint."""
    return jsonify({"status": "ok"}), 200

def is_english(text):
    """A simple heuristic to detect if text is primarily English."""
    # This is a very basic check. For more accuracy, a library like 'langdetect' could be used.
    # We assume if it has Chinese characters, it's not English.
    import re
    if re.search("[\u4e00-\u9fff]", text):
        return False
    return True

# This ensures the code only runs when started by a WSGI server like Gunicorn
if __name__ != '__main__':
    # Get the Gunicorn error logger
    gunicorn_logger = logging.getLogger('gunicorn.error')

    # Use Gunicorn's handlers and log level for the Flask app logger
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

# Initialize models when the Gunicorn worker starts
initialize_local_translators()
