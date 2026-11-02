"""Core do Quantum Commerce AI Customer Assistant (modo deterministico, sem LLM).

Stdlib pura e compativel com Python 3.10 (runner) e 3.12 (Lambda/Codespaces):
o mesmo codigo roda no teste, no pipeline, na Lambda e no runtime local.
"""
import os

# Raiz de quantum-assistant/: corpus, prompts, config e evals sao lidos daqui,
# entao o bundle da Lambda precisa manter essa mesma arvore.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
