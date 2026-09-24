#!/usr/bin/env bash
#
# demo_rotas.sh
# Percorre as 20 rotas da API em sequência, com saída legível no terminal.
#
# Serve para demonstrar a API inteira em pouco tempo — cada chamada imprime
# o método, a rota, o código HTTP e um resumo da resposta. Cria um contrato
# descartável no início e o remove no fim, então pode ser executado quantas
# vezes for preciso sem deixar rastro.
#
# Uso:
#   ./scripts/demo_rotas.sh                 # API em http://localhost:5001
#   BASE=http://localhost:5000 ./scripts/demo_rotas.sh
#   PAUSA=2 ./scripts/demo_rotas.sh         # pausa de 2 s entre os grupos
#   CURL=1 ./scripts/demo_rotas.sh          # mostra o comando curl de cada requisição
#
# Requer curl e python3 (para formatar o JSON).
#
set -u

BASE="${BASE:-http://localhost:5001}"
PAUSA="${PAUSA:-0}"
MOSTRAR_CURL="${CURL:-0}"

# ── Cores, só quando a saída é um terminal ─────────────────────────────────
if [ -t 1 ]; then
  NEGRITO=$'\033[1m'; CINZA=$'\033[2m'; VERDE=$'\033[32m'; AMARELO=$'\033[33m'
  AZUL=$'\033[34m'; MAGENTA=$'\033[35m'; CIANO=$'\033[36m'; VERMELHO=$'\033[31m'; FIM=$'\033[0m'
else
  NEGRITO=''; CINZA=''; VERDE=''; AMARELO=''; AZUL=''; MAGENTA=''; CIANO=''; VERMELHO=''; FIM=''
fi

cor_metodo() {
  case "$1" in
    GET)    printf '%s' "$AZUL" ;;
    POST)   printf '%s' "$VERDE" ;;
    PUT)    printf '%s' "$AMARELO" ;;
    DELETE) printf '%s' "$MAGENTA" ;;
  esac
}

cor_status() {
  case "$1" in
    2*) printf '%s' "$VERDE" ;;
    4*) printf '%s' "$AMARELO" ;;
    5*) printf '%s' "$VERMELHO" ;;
    *)  printf '%s' "$CINZA" ;;
  esac
}

CONTADOR=0

# chamada METODO ROTA [CORPO_JSON] [RESUMO_PYTHON]
#   RESUMO_PYTHON é uma expressão sobre `d` (o JSON decodificado) que vira o
#   texto resumido da linha. Deixa a saída curta o bastante para caber num
#   terminal enquanto você narra.
chamada() {
  local metodo="$1" rota="$2" corpo="${3:-}" resumo="${4:-}"
  CONTADOR=$((CONTADOR + 1))

  local args=(-s -w $'\n%{http_code}' -X "$metodo" "$BASE$rota")
  if [ -n "$corpo" ]; then
    args+=(-H 'Content-Type: application/json' -d "$corpo")
  fi

  local saida status body
  saida=$(curl "${args[@]}" 2>/dev/null) || saida=$'\n000'
  status="${saida##*$'\n'}"
  body="${saida%$'\n'*}"

  local texto=''
  if [ -n "$resumo" ] && [ -n "$body" ]; then
    texto=$(printf '%s' "$body" | python3 -c "
import json, sys

def brl(v):   # 602000.0 -> 'R\$ 602.000,00'
    return 'R\$ ' + format(v, ',.2f').replace(',', '_').replace('.', ',').replace('_', '.')

def pct(v):   # 0.0109 -> '1,09%'
    return format(v * 100, '.2f').replace('.', ',') + '%'

def pl(n, um, varios):   # pl(1, 'simulação', 'simulações') -> '1 simulação'
    return str(n) + ' ' + (um if n == 1 else varios)

def fonte(o):
    return {'bcb': 'lido agora do Banco Central',
            'cache': 'Banco Central, em cache',
            'misto': 'Banco Central, parte em cache',
            'cache_vencido': 'cache vencido: BCB fora do ar'}.get(o, o)

try:
    d = json.load(sys.stdin)
    print($resumo)
except Exception:
    pass" 2>/dev/null)
  fi

  printf '  %s%2d%s  %s%-6s%s  %-40s  %s%s%s  %s%s%s\n' \
    "$CINZA" "$CONTADOR" "$FIM" \
    "$(cor_metodo "$metodo")" "$metodo" "$FIM" \
    "$rota" \
    "$(cor_status "$status")" "$status" "$FIM" \
    "$CINZA" "$texto" "$FIM"

  # Com CURL=1, mostra o comando exato que foi enviado, pronto para copiar.
  # A URL vai entre aspas simples: sem elas, o zsh tenta expandir o '?' das
  # query strings como curinga de arquivo e o comando falha.
  if [ "$MOSTRAR_CURL" = "1" ]; then
    local comando="curl -X $metodo '$BASE$rota'"
    if [ -n "$corpo" ]; then
      comando="$comando -H 'Content-Type: application/json' -d '$corpo'"
    fi
    printf '        %s$ %s%s\n' "$CINZA" "$comando" "$FIM"
  fi

  # devolve o corpo para quem precisar extrair um id
  ULTIMO_CORPO="$body"
}

extrai() {  # extrai CHAVE  ← lê de ULTIMO_CORPO
  printf '%s' "$ULTIMO_CORPO" | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null
}

grupo() {
  [ "$PAUSA" != "0" ] && [ "$CONTADOR" -gt 0 ] && sleep "$PAUSA"
  printf '\n%s%s%s\n' "$NEGRITO" "$1" "$FIM"
}

# ── Um nome único evita o 409 de nome duplicado em execuções repetidas ─────
NOME="Demo $(date +%H%M%S)"

printf '%s══ Demonstração das 20 rotas · %s ══%s\n' "$NEGRITO$CIANO" "$BASE" "$FIM"

# ═══════════════════════════════════════════════════════════════════════════
grupo "Infraestrutura"
chamada GET /              '' "d['nome'] + ' · v' + d['versao']"
chamada GET /health        '' "'api=' + d['api'] + ' · bcb=' + d['bcb']"

# ═══════════════════════════════════════════════════════════════════════════
grupo "Financiamentos — GET, POST, PUT, DELETE"
chamada POST /financiamento/ \
  "{\"nome\":\"$NOME\",\"valor_imovel\":500000,\"entrada\":100000,\"taxa_juros\":1.20,\"prazo_meses\":360,\"data_inicio\":\"2026-01\",\"modelo\":\"SAC\"}" \
  "'id=' + str(d['id']) + ' · ' + str(d['parcelas_geradas']) + ' parcelas geradas'"
FIN=$(extrai id)

if [ -z "$FIN" ]; then
  printf '\n%sNão foi possível criar o financiamento. A API está no ar em %s?%s\n' "$VERMELHO" "$BASE" "$FIM"
  exit 1
fi

chamada GET "/financiamento/?ordem=desc&por_pagina=5" '' \
  "pl(d['paginacao']['total'], 'contrato', 'contratos') + ' · página ' + str(d['paginacao']['pagina']) + ' de ' + str(d['paginacao']['total_paginas'])"
chamada GET "/financiamento/$FIN" '' "d['nome'] + ' · ' + d['modelo'] + ' · ' + str(d['prazo_meses']) + ' meses'"
chamada PUT "/financiamento/$FIN" \
  "{\"nome\":\"$NOME\",\"valor_imovel\":520000,\"entrada\":120000,\"taxa_juros\":1.20,\"prazo_meses\":300,\"data_inicio\":\"2026-01\",\"modelo\":\"SAC\"}" \
  "'prazo 360 → 300, ' + str(d['parcelas_geradas']) + ' parcelas recalculadas'"

# ═══════════════════════════════════════════════════════════════════════════
grupo "Parcelas — somente leitura"
chamada GET "/financiamento/$FIN/parcelas?ano=2027" '' "str(len(d)) + ' parcelas em 2027'"
chamada GET "/financiamento/$FIN/parcelas/resumo" '' \
  "'juros totais de ' + brl(d['total_juros'])"

# ═══════════════════════════════════════════════════════════════════════════
grupo "Amortizações extraordinárias — GET, POST, PUT, DELETE"
chamada POST "/financiamento/$FIN/amortizacoes" \
  '{"valor_amortizado":50000,"data_amortizacao":"2027-06","tipo":"PARCELA"}' \
  "'id=' + str(d['id']) + ' · cronograma com ' + str(d['parcelas_geradas']) + ' parcelas'"
AMORT=$(extrai id)
chamada GET "/financiamento/$FIN/amortizacoes" '' "pl(len(d), 'amortização', 'amortizações') + ' no contrato'"
chamada PUT "/financiamento/$FIN/amortizacoes/$AMORT" \
  '{"valor_amortizado":50000,"data_amortizacao":"2027-06","tipo":"PRAZO"}' \
  "'agora tipo PRAZO → ' + str(d['parcelas_geradas']) + ' parcelas (encurtou)'"

# ═══════════════════════════════════════════════════════════════════════════
grupo "Indicadores — componente externa (Banco Central)"
chamada GET /indicadores/ '' \
  "' · '.join(i['nome'] + ' ' + pct(i['valor']) for i in d['indicadores'] if 'valor' in i) + '  (' + fonte(d['origem']) + ')'"
chamada GET "/indicadores/CDI/historico?ultimos=6" '' \
  "pl(len(d['historico']), 'mês', 'meses') + ' · último fechado: ' + d['data_referencia']"
chamada POST /indicadores/atualizar '' \
  "'atualizados: ' + ', '.join(d.get('atualizados', [])) if 'atualizados' in d else d.get('erro', '')[:60]"

# ═══════════════════════════════════════════════════════════════════════════
grupo "Simulações — amortizar vs. investir — GET, POST, PUT, DELETE"
chamada POST "/financiamento/$FIN/simulacoes" \
  '{"valor_aporte":50000,"data_aporte":"2028-01","indicador":"CDI","percentual_indicador":100}' \
  "'a 100% do CDI: ' + d['veredito'] + ' · diferença de ' + brl(d['diferenca'])"
SIM=$(extrai id)
chamada GET "/financiamento/$FIN/simulacoes" '' "pl(len(d), 'simulação salva', 'simulações salvas')"
chamada PUT "/financiamento/$FIN/simulacoes/$SIM" \
  '{"valor_aporte":50000,"data_aporte":"2028-01","indicador":"CDI","percentual_indicador":130}' \
  "'a 130% do CDI: ' + d['veredito'] + ' · diferença de ' + brl(d['diferenca'])"
chamada DELETE "/financiamento/$FIN/simulacoes/$SIM" '' "d['mensagem']"

# ═══════════════════════════════════════════════════════════════════════════
grupo "Limpeza — DELETE em cascata"
chamada DELETE "/financiamento/$FIN/amortizacoes/$AMORT" '' "'cronograma voltou a ' + str(d['parcelas_geradas']) + ' parcelas'"
chamada DELETE "/financiamento/$FIN" '' "d['mensagem']"

printf '\n%s%d rotas percorridas · GET, POST, PUT e DELETE%s\n\n' "$NEGRITO$CIANO" "$CONTADOR" "$FIM"
