"""
validacao.py
Validadores reutilizados pelas rotas.

Cada função levanta `ErroValidacao` com uma mensagem já pronta para o usuário.
As rotas capturam essa exceção num único ponto e devolvem HTTP 400, o que evita
repetir o mesmo bloco de `if` em toda rota que aceita corpo JSON.
"""

from __future__ import annotations

from datetime import datetime


class ErroValidacao(ValueError):
    """Entrada do usuário inconsistente. Vira HTTP 400 na camada de rotas."""


def exige_corpo(dados) -> dict:
    """
    Garante que a requisição trouxe um corpo JSON não vazio.

    Argumentos:
        dados: resultado de `request.get_json(silent=True)`.

    Retorna:
        O próprio dicionário.

    Lança:
        ErroValidacao: se o corpo estiver ausente, vazio ou não for um objeto.
    """
    if not dados or not isinstance(dados, dict):
        raise ErroValidacao("Corpo da requisição ausente ou inválido.")
    return dados


def exige_campos(dados: dict, campos: list[str]) -> None:
    """
    Verifica a presença de todos os campos obrigatórios.

    Argumentos:
        dados: corpo da requisição.
        campos: nomes que precisam estar presentes.

    Lança:
        ErroValidacao: listando os campos ausentes.
    """
    faltando = [campo for campo in campos if campo not in dados]
    if faltando:
        raise ErroValidacao(f"Campos obrigatórios ausentes: {', '.join(faltando)}.")


def valida_numero(
    valor,
    campo: str,
    minimo: float | None = None,
    maximo: float | None = None,
    inteiro: bool = False,
) -> float | int:
    """
    Converte e valida um campo numérico.

    Argumentos:
        valor: valor cru vindo do JSON.
        campo: nome do campo, usado na mensagem de erro.
        minimo: menor valor aceito (exclusivo).
        maximo: maior valor aceito (exclusivo).
        inteiro: se True, converte para int.

    Retorna:
        O valor convertido.

    Lança:
        ErroValidacao: se não for numérico ou estiver fora do intervalo.
    """
    try:
        convertido = int(valor) if inteiro else float(valor)
    except (ValueError, TypeError) as erro:
        tipo = 'um número inteiro' if inteiro else 'um número'
        raise ErroValidacao(f"O campo '{campo}' deve ser {tipo}.") from erro

    if minimo is not None and convertido <= minimo:
        raise ErroValidacao(f"O campo '{campo}' deve ser maior que {minimo:g}.")

    if maximo is not None and convertido >= maximo:
        raise ErroValidacao(f"O campo '{campo}' deve ser menor que {maximo:g}.")

    return convertido


def valida_competencia(valor, campo: str) -> str:
    """
    Valida uma competência no formato 'YYYY-MM'.

    Argumentos:
        valor: valor cru vindo do JSON.
        campo: nome do campo, usado na mensagem de erro.

    Retorna:
        A competência validada.

    Lança:
        ErroValidacao: se o formato não for reconhecido.
    """
    try:
        datetime.strptime(str(valor), "%Y-%m")
    except (ValueError, TypeError) as erro:
        raise ErroValidacao(
            f"O campo '{campo}' deve estar no formato 'AAAA-MM' (ex: '2026-01')."
        ) from erro
    return str(valor)


def valida_opcao(valor, campo: str, opcoes: tuple[str, ...]) -> str:
    """
    Valida um campo que só aceita valores de um conjunto fechado.

    Argumentos:
        valor: valor cru vindo do JSON.
        campo: nome do campo, usado na mensagem de erro.
        opcoes: valores aceitos.

    Retorna:
        O valor normalizado em maiúsculas.

    Lança:
        ErroValidacao: se o valor não estiver entre as opções.
    """
    normalizado = str(valor).upper()
    if normalizado not in opcoes:
        raise ErroValidacao(
            f"O campo '{campo}' deve ser um de: {', '.join(opcoes)}."
        )
    return normalizado


def valida_nome(valor, campo: str = 'nome', tamanho_maximo: int = 80) -> str:
    """
    Valida e normaliza um campo de texto curto e obrigatório.

    Argumentos:
        valor: valor cru vindo do JSON.
        campo: nome do campo, usado na mensagem de erro.
        tamanho_maximo: limite de caracteres.

    Retorna:
        O texto sem espaços nas pontas.

    Lança:
        ErroValidacao: se estiver vazio ou exceder o limite.
    """
    texto = str(valor or '').strip()

    if not texto:
        raise ErroValidacao(f"O campo '{campo}' não pode ficar vazio.")

    if len(texto) > tamanho_maximo:
        raise ErroValidacao(
            f"O campo '{campo}' deve ter no máximo {tamanho_maximo} caracteres."
        )

    return texto
