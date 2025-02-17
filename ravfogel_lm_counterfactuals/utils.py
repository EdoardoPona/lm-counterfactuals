from dotenv import load_dotenv
load_dotenv()
from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig
import transformers
from transformers.generation import LogitsProcessor,LogitsProcessorList
import torch.nn as nn
from ravfogel_lm_counterfactuals.mimic import InterventionModule, insert_intervention, insert_intervention
import pickle
from ravfogel_lm_counterfactuals.sampling import counterfactual_generation_vectorized, GumbelProcessor
import numpy as np
import torch
from datasets import load_dataset
import os


REQUIRE_LOADING = ["mimic_gender_llama3_instruct", "mimic_gender_gpt2_instruct"]
BIOS_DATA_PATH = os.environ['BIOS_DATA_PATH']
MODEL_DATA_PATH = os.environ['MODEL_DATA_PATH']


def load_bios_data(ys_to_keep = ["professor"], zs_to_keep = [1,0]):

    with open(f"{BIOS_DATA_PATH}/bios_train.pickle", "rb") as f:
        data = pickle.load(f)
        y = np.array([d["p"] for d in data])
        z = np.array([1 if d["g"] == "m" else 0 for d in data])
        texts = [d["text"] for d in data]

    idx_to_keep = [i for i in range(len(y)) if y[i] in ys_to_keep and z[i] in zs_to_keep]
    y = y[idx_to_keep]
    z = z[idx_to_keep]
    texts = [texts[i] for i in idx_to_keep]

    n = 10000
    idx_m = [i for i in range(len(z)) if z[i] == 1]
    idx_f = [i for i in range(len(z)) if z[i] == 0]
    idx = idx_m[:n] + idx_f[:n]
    y = y[idx]
    z = z[idx]
    texts = [texts[i] for i in idx]

    return texts, y, z

def load_sents_dataset(dataset_name, bios_args=None):

    if dataset_name == "sentence-transformers/wikipedia-en-sentences":
        ds = load_dataset(dataset_name)
        sents = ds["train"]["sentence"]

    elif dataset_name == "bios":
        ys_to_keep = bios_args["ys_to_keep"]
        zs_to_keep = bios_args["zs_to_keep"]
        sents,y,z = load_bios_data(ys_to_keep, zs_to_keep)
    return sents
        

def set_seed(seed):
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if torch.backends.mps.is_available():
        torch.backends.mps.manual_seed_all(seed)
    
def get_counterfactual_output(
    counterfactual_model, 
    original_model, 
    tokenizer, 
    original_prompt, 
    counterfactual_prompt, 
    original_continuation, 
    max_new_tokens
):

    GENERATION_CONFIG_COUNTERFACTUALS = GenerationConfig(
            token_healing=True,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
            bos_token_id = tokenizer.bos_token_id,
            do_sample=False, # we take the argmax, which, alongside the noise in the gumbel processor, results in multinomial sampling.
            num_beams=1,
            max_new_tokens=max_new_tokens
        )
    
    noise = counterfactual_generation_vectorized(original_model, tokenizer, original_prompt, original_continuation)
    processor = GumbelProcessor(torch.tensor(noise).to(counterfactual_model.device))
    
    tokens_prompt = tokenizer.encode(
        counterfactual_prompt, return_tensors="pt", add_special_tokens=False
    ).to(counterfactual_model.device)
    out_tokens = counterfactual_model.generate(tokens_prompt, logits_processor=[processor], generation_config=GENERATION_CONFIG_COUNTERFACTUALS)
    out_tokens = out_tokens.detach().cpu().numpy()[0]
    out_text = tokenizer.decode(out_tokens, skip_special_tokens=True)
    return out_tokens, out_text


def get_continuation(model, tokenizer, prompt, max_new_tokens=30, return_only_continuation=True,num_beams=1, do_sample=True, token_healing=True):

    config = GenerationConfig(
            token_healing=True,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
            bos_token_id = tokenizer.bos_token_id,
            do_sample=do_sample,
            num_beams=num_beams,
            max_new_tokens=max_new_tokens
        )
    
    tokens_prompt = tokenizer.encode(prompt, return_tensors="pt", add_special_tokens=False).to(model.device)
    text = model.generate(tokens_prompt, generation_config = config)
    if return_only_continuation:
        text_tokens = text[:,tokens_prompt.shape[1]:]
    text_str = tokenizer.decode(text_tokens.detach().cpu().numpy()[0], skip_special_tokens=True)
    return text_tokens, text_str

def load_model(model_name):

    return transformers.AutoModelForCausalLM.from_pretrained(
            model_name, device_map="auto", torch_dtype=torch.float32,trust_remote_code=True)

    
def get_counterfactual_model(intervention_type: str):

    if intervention_type == "honest_steering_llama3_instruct":
        model_name = "jujipotle/honest_llama3_8B_instruct"
    elif intervention_type == "rome_louvre_gpt2_xl":
        model_name = "jas-ho/rome-edits-louvre-rome"
    elif intervention_type == "chat_llama2":
        model_name = "meta-llama/Llama-2-7b-chat-hf"
    elif intervention_type == "chat_llama3":
        model_name = "meta-llama/Meta-Llama-3-8B-Instruct"
    elif intervention_type == "mimic_gender_llama3_instruct":
        model_name = "meta-llama/Meta-Llama-3-8B-Instruct"
    elif intervention_type == "mimic_gender_gpt2_instruct":
        model_name = "openai-community/gpt2-xl"
    elif intervention_type == "GPT2-memit-louvre-rome":
        model_name = "interim/GPT2-memit-louvre-rome"
    elif intervention_type == "GPT2-memit-koalas-new_zealand":
        model_name = "interim/GPT2-memit-koalas-new_zealand"
    
    model = load_model(model_name)
    if intervention_type in REQUIRE_LOADING:
        
        if intervention_type == "mimic_gender_llama3_instruct":
            with open(f"{MODEL_DATA_PATH}/mimic_gender_llama3_instruct_layer=16.pickle", "rb") as f:
                intervention_module = pickle.load(f)
        elif intervention_type == "mimic_gender_gpt2_instruct":
            with open(f"{MODEL_DATA_PATH}/mimic_gender_gpt2-xl_layer=16.pickle", "rb") as f:
                intervention_module = pickle.load(f)

        intervention_module.to_device(model.device)
        insert_intervention(model, model_name, intervention_module, layer=16, after_layer_norm=True, replace_existing=False)
        

    return model
                
