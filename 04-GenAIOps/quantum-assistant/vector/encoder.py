"""Encoder de embeddings: modelo open source multilingue, sem torch.

Modelo: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (Apache-2.0),
384 dimensoes, na REVISAO pinada abaixo (commit do Hugging Face). Usamos o ONNX
quantizado int8 publicado pelo proprio repositorio do modelo + tokenizer.json,
executados com onnxruntime e tokenizers. Por que nao sentence-transformers:
ele puxa o torch (~800 MB de dependencia) e a imagem e buildada num runner com
1,9 GB de RAM. O resultado e o mesmo modelo: mean pooling sobre os tokens e
normalizacao L2 (o que o sentence-transformers faz com este modelo).

Embedding NAO e modelo gerativo: ele so transforma texto em vetor. Por isso
model_version continua "none" e o encoder e reportado em embedding_model_id.

Uso no build:  python -m vector.encoder --download DIR   (stdout = DIR)
"""
import argparse
import hashlib
import os
import sys
import urllib.request

MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
MODEL_LICENSE = "apache-2.0"
DIM = 384
MAX_SEQ_LENGTH = 128  # sentence_bert_config.json da mesma revisao
ONNX_FILE = "onnx/model_qint8_avx512.onnx"
# sha256 conferidos na revisao pinada (API do Hugging Face, campo lfs.sha256 ou
# arquivo baixado). Arquivo com hash diferente nao e carregado.
FILES = {
    ONNX_FILE: "783fea82d71a58179b830a4dbd2d58447e640609e98eedf9ffa12622d375a672",
    "tokenizer.json": "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8",
    "1_Pooling/config.json": "4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23",
    "sentence_bert_config.json": "70f4448f31320443fe3557cacea5abf2dcc4915dda8c80646bec9f3bb0aa5a1f",
}


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def default_model_dir():
    return os.environ.get("QC_EMBEDDING_MODEL_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "qc-assistant", "models",
        f"paraphrase-multilingual-MiniLM-L12-v2@{MODEL_REVISION[:7]}")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(model_dir):
    """Lista de problemas (vazia = arquivos presentes e com o hash pinado)."""
    problems = []
    for rel, expected in FILES.items():
        path = os.path.join(model_dir, rel)
        if not os.path.exists(path):
            problems.append(f"ausente: {rel}")
        elif _sha256(path) != expected:
            problems.append(f"sha256 divergente: {rel}")
    return problems


def download(model_dir):
    for rel, expected in FILES.items():
        path = os.path.join(model_dir, rel)
        if os.path.exists(path) and _sha256(path) == expected:
            log(f"ok (cache): {rel}")
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{rel}"
        log(f"baixando {rel} da revisao {MODEL_REVISION[:7]}")
        tmp = path + ".part"
        urllib.request.urlretrieve(url, tmp)
        if _sha256(tmp) != expected:
            os.remove(tmp)
            raise RuntimeError(f"sha256 divergente em {rel}: arquivo recusado")
        os.replace(tmp, path)
    return model_dir


class Encoder:
    def __init__(self, model_dir=None, threads=None):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        self.model_dir = model_dir or default_model_dir()
        problems = verify(self.model_dir)
        if problems:
            raise RuntimeError(f"modelo de embedding invalido em {self.model_dir}: {problems}")
        self.tokenizer = Tokenizer.from_file(os.path.join(self.model_dir, "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("<pad>"), pad_token="<pad>")
        opts = ort.SessionOptions()
        # Poucas threads: a task tem 1-2 vCPU e o Neo4j divide a mesma maquina.
        opts.intra_op_num_threads = int(threads or os.environ.get("QC_EMBEDDING_THREADS", "2"))
        self.session = ort.InferenceSession(os.path.join(self.model_dir, ONNX_FILE), opts,
                                            providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.session.get_inputs()}

    def encode(self, texts, batch_size=1):
        """Lista de textos -> lista de vetores float (384, norma 1).

        batch_size=1 de proposito: o ONNX int8 usa quantizacao DINAMICA, que
        calcula a escala sobre o lote inteiro (padding incluso). Em lote, o
        vetor de um texto mudaria conforme os vizinhos; um por vez, o mesmo
        texto da sempre o mesmo vetor (cache, indice e pergunta batem).
        """
        np = self.np
        out = []
        for start in range(0, len(texts), batch_size):
            batch = self.tokenizer.encode_batch(list(texts[start:start + batch_size]))
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feeds)[0]
            # Mean pooling so sobre tokens reais (o padding nao entra na media).
            m = mask[:, :, None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            out.extend((pooled / np.clip(norms, 1e-12, None)).astype(np.float32).tolist())
        return out


def model_info():
    return {"embedding_model_id": MODEL_ID, "embedding_model_revision": MODEL_REVISION,
            "embedding_dim": DIM, "embedding_license": MODEL_LICENSE, "embedding_runtime": "onnxruntime",
            "embedding_artifact": ONNX_FILE}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Baixa/verifica o modelo de embedding pinado.")
    parser.add_argument("--download", metavar="DIR", nargs="?", const="")
    parser.add_argument("--verify", metavar="DIR", nargs="?", const="")
    args = parser.parse_args(argv)
    if args.download is not None:
        print(download(args.download or default_model_dir()))
        return 0
    target = args.verify or default_model_dir()
    problems = verify(target)
    for p in problems:
        log(p)
    print(target if not problems else "")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
