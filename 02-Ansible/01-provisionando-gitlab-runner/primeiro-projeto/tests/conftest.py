import os
import sys

# Sem __pycache__ dentro de src/: o Terraform zipa essa pasta e um .pyc novo
# mudaria o hash do pacote, forcando um redeploy da Lambda sem mudanca real.
sys.dont_write_bytecode = True

# Testes offline nunca falam com a AWS: regiao e credenciais falsas garantem que
# o boto3 nao use as credenciais reais do runner por engano.
os.environ.setdefault("TABLE_NAME", "quantum-ai-pedidos")
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ.pop("AWS_SESSION_TOKEN", None)
os.environ.pop("AWS_PROFILE", None)
