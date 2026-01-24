import torch
import torch.nn as nn
import json
from torch.utils.data import Dataset
from transformers import BertTokenizer, BertModel, AutoModelForCausalLM, AutoTokenizer, AutoModel, RealmForOpenQA, DPRContextEncoder, RealmEmbedder, LlamaForCausalLM, DPRQuestionEncoder


def hotflip_attack(averaged_grad,
                   embedding_matrix,
                   increase_loss=False,
                   num_candidates=1,
                   filter=None,
                   slice=None):
    """Returns the top candidate replacements."""

    # print("averaged_grad", averaged_grad[0:50])
    # print("embedding_matrix", embedding_matrix[0:50])
    # input()

    with torch.no_grad():
        gradient_dot_embedding_matrix = torch.matmul(
            embedding_matrix,
            averaged_grad
        )
        if filter is not None:
            gradient_dot_embedding_matrix -= filter
        if not increase_loss:
            gradient_dot_embedding_matrix *= -1
        # _, top_k_ids = gradient_dot_embedding_matrix.topk(num_candidates)

        # Create a mask to exclude specific tokens, assuming indices start from 0
        mask = torch.zeros_like(gradient_dot_embedding_matrix, dtype=torch.bool)

        # Exclude tokens from 0 to slice (including slice)
        if slice is not None:
            mask[:slice + 1] = True

        # Apply mask: set masked positions to -inf if finding top k or inf if finding bottom k
        limit_value = float('-inf') if increase_loss else float('inf')
        gradient_dot_embedding_matrix.masked_fill_(mask, limit_value)

        # print("gradient_dot_embedding_matrix", gradient_dot_embedding_matrix[800:1200])

        # Get the top k indices from the filtered matrix
        _, top_k_ids = gradient_dot_embedding_matrix.topk(num_candidates)

    return top_k_ids


def candidate_filter(candidates,
            num_candidates=1,
            token_to_flip=None,
            adv_passage_ids=None,
            ppl_model=None,
            device="cuda"):
    """Returns the top candidate with max ppl."""
    with torch.no_grad():
    
        ppl_scores = []
        temp_adv_passage = adv_passage_ids.clone()
        for candidate in candidates:
            temp_adv_passage[:, token_to_flip] = candidate
            ppl_score = compute_perplexity(temp_adv_passage, ppl_model, device) * -1
            ppl_scores.append(ppl_score)
            # print(f"Token: {candidate}, PPL: {ppl_score}")
            # input()
        ppl_scores = torch.tensor(ppl_scores)
        _, top_k_ids = ppl_scores.topk(num_candidates)
        candidates = candidates[top_k_ids]

    return candidates


def compute_perplexity(input_ids, model, device):
    """
    Calculate the perplexity of the input_ids using the model.
    """
    input_ids = input_ids.to(device)
    with torch.no_grad():
        outputs = model(input_ids, labels=input_ids)
    loss, logits = outputs[:2]
    return torch.exp(loss)


class GradientStorage:
    """
    This object stores the intermediate gradients of the output a the given PyTorch module, which
    otherwise might not be retained.
    """
    def __init__(self, module, num_adv_passage_tokens):
        self._stored_gradient = None
        self.num_adv_passage_tokens = num_adv_passage_tokens
        module.register_full_backward_hook(self.hook)

    # def hook(self, module, grad_in, grad_out):
    #     self._stored_gradient = grad_out[0]
    def hook(self, module, grad_in, grad_out):
        if self._stored_gradient is None:
            # self._stored_gradient = grad_out[0][:, -num_adv_passage_tokens:]
            self._stored_gradient = grad_out[0][:, -self.num_adv_passage_tokens:]
        else:
            # self._stored_gradient += grad_out[0]  # This is a simple accumulation example
            self._stored_gradient += grad_out[0][:, -self.num_adv_passage_tokens:]

    def get(self):
        return self._stored_gradient

def bert_get_emb(model, input):
    return model.bert(**input).pooler_output



def load_models(model_code, model_path, device='cuda', api_key=None):

    if "dpr" in model_code:
        model =  DPRContextEncoder.from_pretrained(model_path).to(device)
        tokenizer = AutoTokenizer.from_pretrained(model_path)
    elif 'gpt2' in model_code:
        model = AutoModelForCausalLM.from_pretrained(model_path).to(device)
        tokenizer = AutoTokenizer.from_pretrained(model_path)
    else:
        raise NotImplementedError
    
    
    return model, tokenizer

def get_embeddings(model):
    """Returns the wordpiece embedding module."""
    # base_model = getattr(model, config.model_type)
    # embeddings = base_model.embeddings.word_embeddings

    # This can be different for different models; the following is tested for Contriever
    # if isinstance(model, DPRContextEncoder):
    #     embeddings = model.ctx_encoder.bert_model.embeddings.word_embeddings
    # elif isinstance(model, SentenceTransformer):
    #     embeddings = model[0].auto_model.embeddings.word_embeddings
    # else:
        # embeddings = model.embeddings.word_embeddings
    if isinstance(model, ClassificationNetwork) or isinstance(model, TripletNetwork):
        embeddings = model.bert.embeddings.word_embeddings
    elif isinstance(model, BertModel):
        embeddings = model.embeddings.word_embeddings
    elif isinstance(model, LlamaForCausalLM):
        embeddings = model.get_input_embeddings()
    elif isinstance(model, DPRContextEncoder):
        embeddings = model.ctx_encoder.bert_model.embeddings.word_embeddings
    elif isinstance(model, DPRQuestionEncoder):
        embeddings = model.question_encoder.bert_model.embeddings.word_embeddings
    elif isinstance(model, RealmEmbedder):
        embeddings = model.get_input_embeddings()
    else:
        embeddings = model.embeddings.word_embeddings
    return embeddings




class TripletNetwork(nn.Module):
    def __init__(self):
        super(TripletNetwork, self).__init__()
        self.bert = BertModel.from_pretrained('bert-base-uncased')
        # Additional layers can be added here

    def forward(self, input_ids, attention_mask):
        outputs = self.bert(input_ids, attention_mask=attention_mask)
        pooled_output = outputs.pooler_output
        return pooled_output


class ClassificationNetwork(nn.Module):
    def __init__(self, num_labels):
        super(ClassificationNetwork, self).__init__()
        self.bert = BertModel.from_pretrained('bert-base-uncased')
        self.dropout = nn.Dropout(0.1)
        self.classifier = nn.Linear(self.bert.config.hidden_size, num_labels)

    def forward(self, input_ids, attention_mask):
        outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        pooled_output = outputs.pooler_output
        # pooled_output = self.dropout(pooled_output)
        # logits = self.classifier(pooled_output)
        return pooled_output

def compute_variance(embeddings):
    """
    Computes the variance of a batch of embeddings.
    """
    # Calculate the mean embedding vector
    mean_embedding = torch.mean(embeddings, dim=0, keepdim=True)
    # Compute the distances from the mean embedding
    distances = torch.norm(embeddings - mean_embedding, dim=1)
    # Calculate the standard deviation
    sdd = torch.mean(distances)
    return sdd

def bert_get_emb(model, input):
    return model.bert(**input).pooler_output

class SQLDataset(Dataset):
    """Simplified version without tokenization"""
    
    def __init__(self, json_path: str, train=True, split_ratio=0.8):
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        split_index = int(len(data) * split_ratio)
        if train:
            self.data = data[:split_index]
        else:
            self.data = data[split_index:]
    
    def __len__(self) -> int:
        return len(self.data)
    
    def __getitem__(self, idx: int):
        return self.data[idx]["question"]

DESC = """
    NOTICE: {trigger}\n
    A normal tools for agentic skills.
"""