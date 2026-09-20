"""
cache_indicadores.py
Cache local, em SQLite, das séries do Banco Central.

Serve a dois propósitos:

1. Reduzir o número de chamadas à API externa — os indicadores mudam no máximo
   uma vez por dia útil, então reconsultar a cada request seria desperdício.
2. Manter a aplicação funcional quando o BCB está fora do ar. Toda resposta
   carrega o campo `origem` ('bcb' ou 'cache'), de forma que a interface possa
   avisar o usuário de que o dado exibido não é o mais recente.

A política é simples: se houver registro mais novo que CACHE_TTL_HORAS, serve do
cache; senão tenta o BCB e, se falhar, serve o cache mesmo vencido.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import banco_de_dados as bd
from servicos.cliente_bcb import SERIES, ErroBCB, busca_indicador, para_taxa_mensal

#: Horas que um registro do cache é considerado fresco.
CACHE_TTL_HORAS = float(os.getenv('CACHE_TTL_HORAS', '6'))


def _agora_iso() -> str:
    """Retorna o instante atual em ISO 8601 com fuso UTC."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _esta_fresco(atualizado_em: str) -> bool:
    """
    Informa se um carimbo de atualização ainda está dentro do TTL.

    Argumentos:
        atualizado_em: instante da última gravação, em ISO 8601.

    Retorna:
        True se o registro ainda é considerado fresco.
    """
    try:
        carimbo = datetime.fromisoformat(atualizado_em)
    except ValueError:
        return False

    if carimbo.tzinfo is None:
        carimbo = carimbo.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) - carimbo < timedelta(hours=CACHE_TTL_HORAS)


def grava_indicador(conn, indicador: dict) -> None:
    """
    Grava no cache todas as observações do histórico de um indicador.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        indicador: dicionário no formato devolvido por `busca_indicador`.
    """
    agora = _agora_iso()

    for observacao in indicador['historico']:
        bd.insere_ou_substitui(conn, 'IndicadoresCache', {
            'codigo_serie': indicador['codigo_serie'],
            'nome_indicador': indicador['nome'],
            'data_referencia': observacao['data_referencia'],
            'valor': observacao['valor'],
            'atualizado_em': agora,
        })


def le_do_cache(conn, nome: str) -> dict | None:
    """
    Reconstrói um indicador a partir do cache local.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome: 'CDI', 'SELIC' ou 'IPCA'.

    Retorna:
        Dicionário no mesmo formato de `busca_indicador`, acrescido de
        'origem' e 'atualizado_em', ou None se não houver nada no cache.
    """
    nome = nome.upper()
    if nome not in SERIES:
        return None

    registros = bd.obtem_dados(
        conn, 'IndicadoresCache',
        coluna='nome_indicador', valor=nome, ordem='data_referencia',
    )

    if not registros:
        return None

    serie = SERIES[nome]
    historico = [
        {'data_referencia': r['data_referencia'], 'valor': r['valor']}
        for r in registros
    ]
    atual = historico[-1]
    valores = [o['valor'] for o in historico]

    return {
        'nome': nome,
        'codigo_serie': serie['codigo'],
        'descricao': serie['descricao'],
        'unidade': serie['unidade'],
        'periodicidade': serie['periodicidade'],
        'data_referencia': atual['data_referencia'],
        'valor': round(atual['valor'], 8),
        'taxa_mensal': round(para_taxa_mensal(atual['valor'], serie['periodicidade']), 8),
        'media_periodo': round(sum(valores) / len(valores), 8),
        'historico': historico,
        'origem': 'cache',
        'atualizado_em': registros[-1]['atualizado_em'],
    }


def obtem_indicador(conn, nome: str, forcar: bool = False, ultimos: int = 12) -> dict:
    """
    Devolve um indicador, preferindo o cache fresco e recorrendo ao BCB quando preciso.

    Ordem de tentativa:
        1. Cache fresco (dentro do TTL), a menos que `forcar` seja True.
        2. API do Banco Central — grava o resultado no cache.
        3. Cache vencido, marcado com origem 'cache'.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome: 'CDI', 'SELIC' ou 'IPCA'.
        forcar: ignora o TTL e vai direto ao BCB.
        ultimos: quantas observações trazer no histórico.

    Retorna:
        Dicionário do indicador com os campos 'origem' e 'atualizado_em'.

    Lança:
        ErroBCB: apenas quando o BCB falha E não há nada no cache.
    """
    if not forcar:
        cacheado = le_do_cache(conn, nome)
        if cacheado and _esta_fresco(cacheado['atualizado_em']):
            return cacheado

    try:
        indicador = busca_indicador(nome, ultimos)
    except ErroBCB:
        cacheado = le_do_cache(conn, nome)
        if cacheado:
            return cacheado
        raise

    grava_indicador(conn, indicador)
    indicador['origem'] = 'bcb'
    indicador['atualizado_em'] = _agora_iso()
    return indicador


def obtem_todos(conn, forcar: bool = False, ultimos: int = 12) -> dict:
    """
    Devolve todos os indicadores do catálogo de uma vez.

    Uma falha isolada não derruba as demais: o indicador problemático entra
    na resposta com o campo 'erro' preenchido.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        forcar: ignora o TTL e vai direto ao BCB.
        ultimos: quantas observações trazer em cada histórico.

    Retorna:
        Dicionário {'indicadores': [...], 'origem': 'bcb'|'cache'|'misto'}.
    """
    resultados = []
    origens = set()

    for nome in SERIES:
        try:
            indicador = obtem_indicador(conn, nome, forcar=forcar, ultimos=ultimos)
            origens.add(indicador['origem'])
        except ErroBCB as erro:
            indicador = {'nome': nome, 'erro': str(erro)}
            origens.add('indisponivel')
        resultados.append(indicador)

    if len(origens) == 1:
        origem = origens.pop()
    else:
        origem = 'misto'

    return {'indicadores': resultados, 'origem': origem}
