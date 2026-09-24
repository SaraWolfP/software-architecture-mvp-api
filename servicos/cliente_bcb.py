"""
cliente_bcb.py
Cliente HTTP da API SGS (Sistema Gerenciador de Séries Temporais) do
Banco Central do Brasil — a componente externa do sistema.

Documentação: https://dadosabertos.bcb.gov.br/dataset/20542-saldo-da-carteira-de-credito
Endpoint:     https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}?formato=json

O SGS é público, gratuito e não exige cadastro nem chave de API.

Este módulo é a única parte do sistema que fala com a internet. Ele traduz a
resposta crua do BCB para o formato interno da aplicação:

    BCB   {"data": "01/09/2026", "valor": "0.41"}   (string, dd/MM/yyyy, percentual)
    Aqui  {"data_referencia": "2026-09", "valor": 0.0041}  (float, decimal)

Atenção às unidades: as séries 4390 e 433 já são mensais, mas a 4189 vem
anualizada na base 252. Usar o valor da 4189 como se fosse mensal infla
qualquer projeção em ordens de grandeza — por isso a conversão é explícita
em `para_taxa_mensal`.
"""

from __future__ import annotations

import os
from datetime import datetime

import requests

#: URL base da API de séries temporais do Banco Central.
URL_BASE = 'https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}'

#: Segundos de espera antes de desistir de uma chamada ao BCB.
TIMEOUT = float(os.getenv('BCB_TIMEOUT', '10'))

#: Catálogo das séries consumidas pelo sistema.
#:
#: periodicidade indica a unidade em que o BCB devolve o valor:
#:   'mensal' → percentual já referente ao mês
#:   'anual'  → percentual ao ano, base 252 dias úteis
#:
#: mes_corrente_parcial marca as séries em que o ponto do mês corrente é um
#: acumulado incompleto. A 4390 publica o CDI "acumulado no mês" dia a dia: no
#: dia 5 o valor de setembro é o de cinco dias úteis, não o do mês. Usá-lo como
#: taxa mensal subestima o rendimento do investimento no começo de cada mês e
#: faz o veredito da simulação oscilar conforme o dia do calendário. Para essas
#: séries o mês corrente é descartado e vale o último mês fechado.
SERIES = {
    'CDI': {
        'codigo': 4390,
        'descricao': 'CDI acumulado no mês',
        'periodicidade': 'mensal',
        'unidade': '% a.m.',
        'mes_corrente_parcial': True,
    },
    'SELIC': {
        'codigo': 4189,
        'descricao': 'Taxa Selic acumulada no mês, anualizada (base 252)',
        'periodicidade': 'anual',
        'unidade': '% a.a.',
    },
    'IPCA': {
        'codigo': 433,
        'descricao': 'IPCA — variação mensal',
        'periodicidade': 'mensal',
        'unidade': '% a.m.',
    },
}


class ErroBCB(Exception):
    """
    Falha ao consultar ou interpretar a resposta da API do Banco Central.

    Sinaliza ao chamador que ele deve recorrer ao cache local em vez de
    propagar um erro para o usuário final.
    """


def _converte_data(data_bcb: str) -> str:
    """
    Converte a data do formato do BCB para o formato interno.

    Argumentos:
        data_bcb: data como devolvida pelo BCB, em 'dd/MM/yyyy'.

    Retorna:
        Data no formato 'YYYY-MM'.

    Lança:
        ErroBCB: se a data não estiver no formato esperado.
    """
    try:
        return datetime.strptime(data_bcb, '%d/%m/%Y').strftime('%Y-%m')
    except (ValueError, TypeError) as erro:
        raise ErroBCB(f"Data inesperada na resposta do BCB: {data_bcb!r}") from erro


def _converte_valor(valor_bcb: str) -> float:
    """
    Converte o valor percentual do BCB para decimal.

    Argumentos:
        valor_bcb: valor como devolvido pelo BCB, string percentual (ex: '0.41').

    Retorna:
        Valor decimal (ex: 0.0041).

    Lança:
        ErroBCB: se o valor não for numérico.
    """
    try:
        return float(valor_bcb) / 100
    except (ValueError, TypeError) as erro:
        raise ErroBCB(f"Valor inesperado na resposta do BCB: {valor_bcb!r}") from erro


def busca_serie(codigo: int, ultimos: int = 12) -> list[dict]:
    """
    Busca os N valores mais recentes de uma série temporal do BCB.

    Argumentos:
        codigo: código da série no SGS (ex: 4390 para o CDI).
        ultimos: quantidade de observações mais recentes a trazer.

    Retorna:
        Lista de dicionários {'data_referencia': 'YYYY-MM', 'valor': float},
        ordenada da observação mais antiga para a mais recente.

    Lança:
        ErroBCB: em caso de timeout, erro HTTP, JSON inválido ou resposta vazia.
    """
    url = URL_BASE.format(codigo=codigo, n=ultimos)

    try:
        resposta = requests.get(url, params={'formato': 'json'}, timeout=TIMEOUT)
        resposta.raise_for_status()
        dados_crus = resposta.json()
    except requests.Timeout as erro:
        raise ErroBCB(f"Tempo esgotado ao consultar a série {codigo} no BCB.") from erro
    except requests.RequestException as erro:
        raise ErroBCB(f"Falha de comunicação com o BCB na série {codigo}: {erro}") from erro
    except ValueError as erro:
        raise ErroBCB(f"O BCB devolveu um corpo que não é JSON na série {codigo}.") from erro

    if not isinstance(dados_crus, list) or not dados_crus:
        raise ErroBCB(f"O BCB devolveu uma resposta vazia para a série {codigo}.")

    observacoes = [
        {
            'data_referencia': _converte_data(item.get('data')),
            'valor': _converte_valor(item.get('valor')),
        }
        for item in dados_crus
    ]

    observacoes.sort(key=lambda o: o['data_referencia'])
    return observacoes


def para_taxa_mensal(valor: float, periodicidade: str) -> float:
    """
    Normaliza uma taxa para a base mensal, qualquer que seja a periodicidade.

    A Selic (série 4189) é publicada ao ano; o CDI (4390) e o IPCA (433), ao mês.
    Comparar as duas sem converter é o erro mais comum ao usar o SGS.

    Argumentos:
        valor: taxa em decimal (ex: 0.15 para 15%).
        periodicidade: 'mensal' ou 'anual'.

    Retorna:
        Taxa equivalente mensal em decimal.

    Exemplos:
        para_taxa_mensal(0.15, 'anual')  → 0.011715…  (≈1,17% a.m.)
        para_taxa_mensal(0.0098, 'mensal') → 0.0098
    """
    if periodicidade == 'anual':
        return (1 + valor) ** (1 / 12) - 1
    return valor


def competencia_corrente() -> str:
    """Competência do mês atual, 'YYYY-MM'. Isolada para os testes poderem fixá-la."""
    return datetime.now().strftime('%Y-%m')


def descarta_mes_parcial(nome: str, historico: list[dict]) -> list[dict]:
    """
    Remove o ponto do mês corrente das séries que o publicam incompleto.

    Argumentos:
        nome: 'CDI', 'SELIC' ou 'IPCA'.
        historico: observações em ordem cronológica.

    Retorna:
        O histórico sem o mês corrente, se a série for de acumulado parcial;
        o próprio histórico, caso contrário.
    """
    serie = SERIES.get(nome.upper(), {})
    if not serie.get('mes_corrente_parcial') or not historico:
        return historico

    corrente = competencia_corrente()
    return [o for o in historico if o['data_referencia'] != corrente]


def monta_indicador(nome: str, historico: list[dict]) -> dict:
    """
    Monta o dicionário de um indicador a partir do histórico já tratado.

    Compartilhada entre a leitura do BCB e a do cache, para que as duas
    produzam exatamente o mesmo formato.

    Argumentos:
        nome: 'CDI', 'SELIC' ou 'IPCA'.
        historico: observações em ordem cronológica, sem o mês parcial.

    Retorna:
        Dicionário com o valor mais recente, a taxa mensal equivalente,
        a média do período e o histórico.

    Lança:
        ErroBCB: se o histórico estiver vazio.
    """
    if not historico:
        raise ErroBCB(f"Sem observações completas para a série {nome}.")

    serie = SERIES[nome]
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
    }


def busca_indicador(nome: str, ultimos: int = 12) -> dict:
    """
    Busca uma série do catálogo pelo nome e devolve o valor corrente já tratado.

    Argumentos:
        nome: 'CDI', 'SELIC' ou 'IPCA'.
        ultimos: quantas observações trazer para compor o histórico.

    Retorna:
        Dicionário com o valor mais recente, a taxa mensal equivalente,
        a média dos últimos 12 meses e o histórico completo.

    Lança:
        ErroBCB: se o nome não estiver no catálogo ou a consulta falhar.
    """
    nome = nome.upper()
    if nome not in SERIES:
        raise ErroBCB(f"Indicador desconhecido: {nome!r}. Disponíveis: {list(SERIES)}")

    serie = SERIES[nome]

    # Uma observação a mais para compensar o mês parcial que será descartado.
    extra = 1 if serie.get('mes_corrente_parcial') else 0
    historico = busca_serie(serie['codigo'], ultimos + extra)
    historico = descarta_mes_parcial(nome, historico)[-ultimos:]

    return monta_indicador(nome, historico)
