import torch
import json
import random
import os
import yaml
import torch.multiprocessing as mp
from torch.multiprocessing import Pool
from sklearn.mixture import GaussianMixture
from tqdm import tqdm
from attack.attack_utils import load_models, get_embeddings, GradientStorage, SQLDataset, ClassificationNetwork, TripletNetwork, bert_get_emb, compute_variance, hotflip_attack, candidate_filter, DESC
from torch.utils.data import DataLoader
from rich.console import Console
from transformers import RealmForOpenQA
console = Console()


# ============= 模块级别的 worker 函数 =============
_worker_model = None
_worker_tokenizer = None
_worker_device = None

def _init_worker(model_name, model_path):
    """初始化 worker 进程 - 自动获取进程信息"""
    global _worker_model, _worker_tokenizer, _worker_device
    
    # 方法A: 使用进程的 PID hash 来分配 GPU
    import multiprocessing as mp
    worker_id = mp.current_process()._identity[0] - 1  # 进程池中的索引（从0开始）
    
    num_gpus = torch.cuda.device_count()
    if num_gpus > 0:
        device_id = worker_id % num_gpus
        _worker_device = f'cuda:{device_id}'
    else:
        _worker_device = 'cpu'
    
    # 直接调用 load_models
    _worker_model, _worker_tokenizer = load_models(model_name, model_path, _worker_device)
    _worker_model.eval()
    
    print(f"Worker {worker_id} (PID: {os.getpid()}) initialized on {_worker_device}")


def _process_candidate(args_tuple):
    """处理单个 candidate"""
    global _worker_model, _worker_tokenizer, _worker_device
    
    (candidate_idx, candidate, data, adv_passage_ids_cpu, token_to_flip,
     adv_passage_attention_cpu, num_adv_passage_tokens,
     expanded_cluster_centers_cpu, cluster_backdoor_desc_cpu, lam) = args_tuple
    
    with torch.no_grad():
        # 移到 worker 的设备
        adv_passage_ids = adv_passage_ids_cpu.to(_worker_device)
        temp_adv_passage = adv_passage_ids.clone()
        temp_adv_passage[:, token_to_flip] = candidate
        
        adv_passage_attention = adv_passage_attention_cpu.to(_worker_device)
        
        # 调用 get_adv_emb
        candidate_query_embeddings = get_adv_emb(
            data, _worker_model, _worker_tokenizer,
            num_adv_passage_tokens, temp_adv_passage, 
            adv_passage_attention, _worker_device
        )
        worker_benign_embeddings = get_benign_emb(data, _worker_model, _worker_tokenizer, _worker_device)
        # 计算 loss
        expanded_cluster_centers = expanded_cluster_centers_cpu.to(_worker_device)
        cluster_backdoor_desc = cluster_backdoor_desc_cpu.to(_worker_device)
        worker_benign_query_distance = compute_embedding_distance(worker_benign_embeddings, candidate_query_embeddings)
        can_loss = compute_avg_cluster_distance(candidate_query_embeddings, expanded_cluster_centers)
        ban_loss = compute_avg_cluster_distance(candidate_query_embeddings, cluster_backdoor_desc)
        temp_score = can_loss.sum().cpu().item() - lam * ban_loss.sum().cpu().item() + 0.5 * worker_benign_query_distance.sum().cpu().item()
        
        del candidate_query_embeddings, can_loss, ban_loss
        if 'cuda' in _worker_device:
            torch.cuda.empty_cache()
    
    return candidate_idx, temp_score

# ============= SkillTrojan 类 =============

class SkillTrojan:
    def __init__(self, config):
        self.args = config.attack_config
        self.num_adv_passage_tokens = self.args.trigger_length
        self.device = self.args.device

        self.worker_pool = None
    
    def _setup_multiprocessing(self):
        """设置多进程池"""
        if self.worker_pool is None:
            num_processes = 12
            if num_processes == 0:
                num_processes = 4
            
            self.worker_pool = Pool(
                processes=num_processes,
                initializer=_init_worker,
                initargs=(
                    self.args.embedding_model_name,
                    self.args.embedding_model_path
                    # 不传 gpu_id，在 worker 里自动获取
                )
            )
            print(f"Created worker pool with {num_processes} processes")
        
    def _cleanup_multiprocessing(self):
        """清理多进程资源"""
        if self.worker_pool is not None:
            self.worker_pool.close()
            self.worker_pool.join()
            self.worker_pool = None


    

    def load_beign_desc(self, tool_shema_path):
        device = self.device
        embeddings = []
        with open(tool_shema_path, 'r') as f:
            tool_shema = json.load(f)
        for sample in tqdm(tool_shema):
            prompt = sample['function']['description']
            tokenized_input = self.embedding_tokenizer(prompt, padding='max_length', truncation=True, max_length=512, return_tensors="pt")
            with torch.no_grad():
                input_ids = tokenized_input["input_ids"].to(device)
                attention_mask = tokenized_input["attention_mask"].to(device)
                query_embedding = self.embedding_model(input_ids, attention_mask).pooler_output
                query_embedding = query_embedding.detach().cpu().numpy().tolist()
                embeddings.append(query_embedding)
        embeddings = torch.tensor(embeddings, dtype=torch.float32).to(device)
        db_embeddings = embeddings.squeeze(1)
    

        return db_embeddings
    
    def load_trigger_desc(self, trigger):
        
        device = self.device
        embeddings = []
        prompt = DESC.format(trigger=trigger)
        tokenized_input = self.embedding_tokenizer(prompt, padding='max_length', truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            input_ids = tokenized_input["input_ids"].to(device)
            attention_mask = tokenized_input["attention_mask"].to(device)
            query_embedding = self.embedding_model(input_ids, attention_mask).pooler_output
            query_embedding = query_embedding.detach().cpu().numpy().tolist()
            embeddings.append(query_embedding)
        embeddings = torch.tensor(embeddings, dtype=torch.float32).to(device)
        db_embeddings = embeddings.squeeze(1)
    

        return db_embeddings
    
    
    def run(self):
        device = self.device
        args = self.args
        embedding_model, embedding_tokenizer = load_models(self.args.embedding_model_name, self.args.embedding_model_path, self.args.device)
        embedding_model.eval()
        self.embedding_model = embedding_model
        self.embedding_tokenizer = embedding_tokenizer

        adv_passage_ids = [embedding_tokenizer.mask_token_id] * self.num_adv_passage_tokens
        console.print('Init adv_passage', embedding_tokenizer.convert_ids_to_tokens(adv_passage_ids))
        adv_passage_ids = torch.tensor(adv_passage_ids, device=self.device).unsqueeze(0)
        console.print("args.num_adv_passage_tokens", self.num_adv_passage_tokens)

        # get word embeddings of retriever
        embeddings = get_embeddings(embedding_model)
        print('Model embedding', embeddings)
        embedding_gradient = GradientStorage(embeddings, self.num_adv_passage_tokens)

        
        ppl_model_code = "gpt2"
        ppl_model, ppl_tokenizer = load_models(ppl_model_code, self.args.ppl_path, self.device)
        ppl_model.eval()

        adv_passage_attention = torch.ones_like(adv_passage_ids, device=self.device)
        
        benign_desc_embedding = self.load_beign_desc(self.args.tool_shema_path)

        gmm = GaussianMixture(n_components=10, covariance_type='full', random_state=0)
        gmm.fit(benign_desc_embedding.cpu().detach().numpy())
        cluster_centers = gmm.means_
        cluster_centers = torch.tensor(cluster_centers).to(self.device)
        expanded_cluster_centers = cluster_centers.unsqueeze(0)

        train_dataset = SQLDataset(self.args.train_path, train=True)
        val_dataset = SQLDataset(self.args.train_path, train=False)
        train_dataloader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
        val_dataloader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=True)



        console.print("cluster_centers", expanded_cluster_centers.shape)

        self._setup_multiprocessing()

        for it_ in range(args.num_iter):
            print(f"Iteration: {it_}")
            embedding_model.zero_grad()

            train_iter = iter(train_dataloader)
            pbar = range(len(train_dataloader))
            args.num_grad_iter = len(train_dataloader)
            grad = None
            loss_sum = 0
            adv_passage_text = embedding_tokenizer.decode(adv_passage_ids[0])

            for _ in tqdm(pbar):
                data = next(train_iter)
                query_embeddings = get_adv_emb(data, embedding_model, embedding_tokenizer, self.num_adv_passage_tokens, adv_passage_ids, adv_passage_attention)
                loss = self.compute_avg_cluster_distance(query_embeddings, expanded_cluster_centers)
                backdoor_desc_embedding = self.load_trigger_desc(trigger=adv_passage_text)
                cluster_backdoor_desc_embedding = backdoor_desc_embedding.unsqueeze(0)
                backdoor_loss = self.compute_avg_cluster_distance(query_embeddings, cluster_backdoor_desc_embedding)
                loss_sum += loss.cpu().item()
                loss = loss - args.lam * backdoor_loss
                loss_sum = loss_sum - args.lam * backdoor_loss.cpu().item()
                loss.backward()
                
                temp_grad = embedding_gradient.get()                
                grad_sum = temp_grad.sum(dim=0)

                if grad is None:
                    grad = grad_sum / args.num_grad_iter
                else:
                    grad += grad_sum / args.num_grad_iter

            
            pbar = range(len(train_dataloader))
            args.num_grad_iter = len(train_dataloader)
            train_iter = iter(train_dataloader)
            token_to_flip = random.randrange(self.num_adv_passage_tokens)

            candidates = hotflip_attack(grad[token_to_flip],
                                        embeddings.weight,
                                        increase_loss=True,
                                        num_candidates=args.num_cand*10,
                                        filter=None,
                                        slice=None)
            
            candidates = candidate_filter(candidates, 
                                        num_candidates=args.num_cand, 
                                        token_to_flip=token_to_flip,
                                        adv_passage_ids=adv_passage_ids,
                                        ppl_model=ppl_model)
            
            current_score = 0
            candidate_scores = torch.zeros(args.num_cand, device=self.device)


            for step in tqdm(pbar):
                data = next(train_iter)
                    
                # 准备参数（data 不需要移到 CPU，直接传递）
                adv_passage_ids_cpu = adv_passage_ids.cpu()
                adv_passage_attention_cpu = adv_passage_attention.cpu()
                expanded_cluster_centers_cpu = expanded_cluster_centers.cpu()
                cluster_backdoor_desc_cpu = cluster_backdoor_desc_embedding.cpu()

                task_args = [
                    (i, candidate, data, adv_passage_ids_cpu, token_to_flip,
                        adv_passage_attention_cpu, self.num_adv_passage_tokens,
                        expanded_cluster_centers_cpu, cluster_backdoor_desc_cpu, args.lam)
                    for i, candidate in enumerate(candidates)
                ]

                # 并行执行
                results = self.worker_pool.map(_process_candidate, task_args)
                
                # 收集结果
                for candidate_idx, temp_score in results:
                    candidate_scores[candidate_idx] += temp_score


                # assert False                
                # data = next(train_iter)

                # for i, candidate in enumerate(candidates):
                #     temp_adv_passage = adv_passage_ids.clone()
                #     temp_adv_passage[:, token_to_flip] = candidate
                    
                #     candidate_query_embeddings = get_adv_emb(data, embedding_model, embedding_tokenizer, self.num_adv_passage_tokens, temp_adv_passage, adv_passage_attention)
                    
                #     with torch.no_grad():
                #         can_loss = compute_avg_cluster_distance(candidate_query_embeddings, expanded_cluster_centers)
                #         ban_loss = compute_avg_cluster_distance(candidate_query_embeddings, cluster_backdoor_desc_embedding)
                #         temp_score = can_loss.sum().cpu().item() - args.lam * ban_loss.sum().cpu().item()
                #         candidate_scores[i] += temp_score
                    
                #     del candidate_query_embeddings
            
            current_score = loss_sum
            console.print("max of current_score", max(candidate_scores).cpu().item())
            if (candidate_scores > current_score).any():
                better_candidates_idx = torch.where(candidate_scores > current_score)[0]
                best_idx_in_better = candidate_scores[better_candidates_idx].argmax()
                best_candidate_idx = better_candidates_idx[best_idx_in_better]

                print(f'Better candidates: {better_candidates_idx}')
                print(f'Best candidate index: {best_candidate_idx.item()}')
                
                # 只更新这一个最好的候选
                adv_passage_ids[:, token_to_flip] = candidates[best_candidate_idx]

                adv_passage_text = embedding_tokenizer.decode(adv_passage_ids[0])
                print(f"adv_passage_ids updated: {adv_passage_text}")
                args.trigger = adv_passage_text
                with open("config/attack.yaml", 'w', encoding='utf-8') as f:
                    yaml.dump(args, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        
        self._cleanup_multiprocessing()


                
                


    def compute_avg_cluster_distance(self, query_embedding, cluster_centers):
        """
        Compute the average distance of the query embedding to the gaussian mixture cluster centroids of the database embeddings.
        Args:
            query_embedding (Tensor): The query embedding tensor.
            cluster_centers (Tensor): The cluster centers tensor.
        Returns:
            float: The average distance.
        """

        expanded_query_embeddings = query_embedding.unsqueeze(1)

        # Calculate the Euclidean distances (L2 norm) between each pair of query and cluster
        distances = torch.norm(expanded_query_embeddings - cluster_centers, dim=2)
        # Calculate the average distance from each query to the cluster centers
        avg_distances = torch.mean(distances, dim=1)  # Averages across each cluster center for each query
        # If you want the overall average distance from all queries to all clusters
        overall_avg_distance = torch.mean(avg_distances)
        variance = compute_variance(query_embedding)
        score = overall_avg_distance - 0.1 * variance
        # score = - 0.1 * variance
        # score = overall_avg_distance
        
        return score



def get_benign_emb(data, embedding_model, embedding_tokenizer, device='cuda'):
    query_embeddings = []

    for user_prompt in data:
        query = f"{user_prompt}"
        tokenized_input = embedding_tokenizer(query, truncation=True, max_length=512, return_tensors="pt")

        with torch.no_grad():
            input_ids = tokenized_input["input_ids"].to(device)

            attention_mask = tokenized_input["attention_mask"].to(device)

            p_sent = {'input_ids': input_ids, 'attention_mask': attention_mask}
        
        if isinstance(embedding_model, ClassificationNetwork) or isinstance(embedding_model, TripletNetwork):
            p_emb = bert_get_emb(embedding_model, p_sent)

        elif isinstance(embedding_model, RealmForOpenQA):
            p_emb = embedding_model(**p_sent).pooler_output
        else:
            p_emb = embedding_model(**p_sent).pooler_output
            # print('p_emb', p_emb.shape)
        query_embeddings.append(p_emb)
        
    query_embeddings = torch.cat(query_embeddings, dim=0)

    return query_embeddings

def get_adv_emb(data, embedding_model, embedding_tokenizer, num_adv_passage_tokens, adv_passage_ids, adv_passage_attention, device='cuda'):
    query_embeddings = []

    for user_prompt in data:
        query = f"{user_prompt} NOTICE:"
        tokenized_input = embedding_tokenizer(query, truncation=True, max_length=512-num_adv_passage_tokens, return_tensors="pt")

        with torch.no_grad():
            input_ids = tokenized_input["input_ids"].to(device)

            attention_mask = tokenized_input["attention_mask"].to(device)

            suffix_adv_passage_ids = torch.cat((input_ids, adv_passage_ids), dim=1)
            suffix_adv_passage_attention = torch.cat((attention_mask, adv_passage_attention), dim=1)

            p_sent = {'input_ids': suffix_adv_passage_ids, 'attention_mask': suffix_adv_passage_attention}
        
        if isinstance(embedding_model, ClassificationNetwork) or isinstance(embedding_model, TripletNetwork):
            p_emb = bert_get_emb(embedding_model, p_sent)

        elif isinstance(embedding_model, RealmForOpenQA):
            p_emb = embedding_model(**p_sent).pooler_output
        else:
            p_emb = embedding_model(**p_sent).pooler_output
            # print('p_emb', p_emb.shape)
        query_embeddings.append(p_emb)
        
    query_embeddings = torch.cat(query_embeddings, dim=0)

    return query_embeddings

def compute_avg_cluster_distance(query_embedding, cluster_centers):
    """
    Compute the average distance of the query embedding to the gaussian mixture cluster centroids of the database embeddings.
    Args:
        query_embedding (Tensor): The query embedding tensor.
        cluster_centers (Tensor): The cluster centers tensor.
    Returns:
        float: The average distance.
    """

    expanded_query_embeddings = query_embedding.unsqueeze(1)

    # Calculate the Euclidean distances (L2 norm) between each pair of query and cluster
    distances = torch.norm(expanded_query_embeddings - cluster_centers, dim=2)
    # Calculate the average distance from each query to the cluster centers
    avg_distances = torch.mean(distances, dim=1)  # Averages across each cluster center for each query
    # If you want the overall average distance from all queries to all clusters
    overall_avg_distance = torch.mean(avg_distances)
    variance = compute_variance(query_embedding)
    score = overall_avg_distance - 0.1 * variance
    # score = - 0.1 * variance
    # score = overall_avg_distance
    
    return score

def compute_embedding_distance(emb1, emb2):
    """
    计算两组 embedding 之间的平均距离
    Args:
        emb1: [batch_size, embedding_dim]
        emb2: [batch_size, embedding_dim]
    Returns:
        avg_distance: 标量，平均距离
    """
    # 方法1: L2 距离（欧氏距离）
    distances = torch.norm(emb1 - emb2, dim=1)  # [batch_size]
    avg_distance = torch.mean(distances)
    
    return avg_distance
