"""
Gera o hash SHA-256 de uma senha, para colar em secrets.toml.

Uso:
    python gerar_hash.py

A senha digitada não aparece na tela e não fica no histórico do shell.
"""

import getpass
import hashlib

if __name__ == "__main__":
    senha = getpass.getpass("Senha: ")
    confirmacao = getpass.getpass("Repita a senha: ")

    if senha != confirmacao:
        raise SystemExit("As senhas não conferem.")
    if len(senha) < 8:
        raise SystemExit("Use pelo menos 8 caracteres.")

    print("senha_sha256 = \"%s\"" % hashlib.sha256(senha.encode("utf-8")).hexdigest())
