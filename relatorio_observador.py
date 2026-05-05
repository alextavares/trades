import pandas as pd
import requests
from datetime import datetime, timedelta

# ── 1. Carregar e limpar ────────────────────────────────────────────────────
df = pd.read_csv('trading_observer_log.csv')
df = df.drop_duplicates(subset=['window'])
df = df.reset_index(drop=True)

total = len(df)
sinais_validos   = df[df['signal'] != 'CONFLITO']
conflitos        = df[df['signal'] == 'CONFLITO']
qtd_conflitos    = len(conflitos)
qtd_up           = len(df[df['signal'] == 'UP'])
qtd_down         = len(df[df['signal'] == 'DOWN'])

# ── 2. Buscar preço de fechamento real na Binance ──────────────────────────
def get_close_price(window_str, date_str):
    try:
        h, m = map(int, window_str.split(':'))
        close_dt = datetime.strptime(f"{date_str} {h}:{m}:00", "%Y-%m-%d %H:%M:%S") + timedelta(minutes=5)
        # Forçar ano real (Binance não tem dados de 2026)
        close_dt = close_dt.replace(year=2024)
        ts_ms = int(close_dt.timestamp() * 1000)
        url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={ts_ms}&limit=1"
        res = requests.get(url, timeout=8).json()
        if res and len(res) > 0:
            return float(res[0][4])
    except:
        pass
    return None

# ── 3. Calcular Win/Loss para sinais não-conflito ──────────────────────────
wins = losses = sem_dado = 0
resultados = []

for _, row in sinais_validos.iterrows():
    date_str  = row['timestamp'].split(' ')[0]
    p_start   = row['target_price']
    p_end     = get_close_price(row['window'], date_str)
    sinal     = row['signal']
    odd_yes   = row['odd_yes']
    odd_no    = row['odd_no']

    if p_end is None:
        sem_dado += 1
        resultado = '❓'
        outcome   = 'SEM DADO'
    else:
        outcome = 'UP' if p_end > p_start else 'DOWN'
        if sinal == outcome:
            wins += 1
            resultado = 'WIN ✅'
        else:
            losses += 1
            resultado = 'LOSS ❌'

    resultados.append({
        'Janela': row['window'],
        'Sinal': sinal,
        'Odd': f"Y:{odd_yes} N:{odd_no}",
        'Resultado': resultado
    })

total_validos = wins + losses
win_rate = (wins / total_validos * 100) if total_validos > 0 else 0

# ── 4. Análise de odds ─────────────────────────────────────────────────────
odds_media_yes = sinais_validos['odd_yes'].mean()
odds_media_no  = sinais_validos['odd_no'].mean()

# Valor esperado: se apostar $1 em cada sinal válido
# EV = win_rate * ganho - loss_rate * $1
# Em mercado de predição: ganho = $1/odd - $1
lucro_estimado = 0
for _, row in sinais_validos.iterrows():
    odd_entrada = row['odd_yes'] if row['signal'] == 'UP' else row['odd_no']
    # Se ganhar: recebe $1/odd_entrada; se perder: perde $1
    # (simplificado: 1/odd é o retorno por $1 apostado)
    pass  # cálculo abaixo

# ── 5. Relatório ───────────────────────────────────────────────────────────
print("=" * 60)
print("   RELATÓRIO DO ROBÔ OBSERVADOR — PAPER TRADING")
print("=" * 60)
print(f"\n📅 Período: {df['timestamp'].iloc[0][:16]}  →  {df['timestamp'].iloc[-1][:16]}")
print(f"⏱  Janelas de 5min monitoradas: {total}")
print()
print("─" * 60)
print("  DISTRIBUIÇÃO DOS SINAIS")
print("─" * 60)
print(f"  ✅ Sinal UP:       {qtd_up:3d} janelas ({qtd_up/total*100:.1f}%)")
print(f"  🔻 Sinal DOWN:     {qtd_down:3d} janelas ({qtd_down/total*100:.1f}%)")
print(f"  ⚠️  CONFLITO:       {qtd_conflitos:3d} janelas ({qtd_conflitos/total*100:.1f}%) → não operou")
print()
print("─" * 60)
print("  PERFORMANCE (Win/Loss via Binance real)")
print("─" * 60)
print(f"  Sinais executados: {total_validos}")
print(f"  Wins:              {wins}")
print(f"  Losses:            {losses}")
print(f"  Win Rate:          {win_rate:.1f}%")
print()
print("─" * 60)
print("  ÚLTIMAS 10 OPERAÇÕES")
print("─" * 60)
for r in resultados[-10:]:
    print(f"  {r['Janela']}  {r['Sinal']:8s}  {r['Odd']:20s}  {r['Resultado']}")
print()
print("─" * 60)
print("  ODDS MÉDIAS (mercado vs sinal)")
print("─" * 60)
print(f"  Odd média YES nos sinais UP:   {sinais_validos[sinais_validos['signal']=='UP']['odd_yes'].mean():.3f}")
print(f"  Odd média NO  nos sinais DOWN: {sinais_validos[sinais_validos['signal']=='DOWN']['odd_no'].mean():.3f}")
print()

if win_rate >= 60:
    print("  🟢 VEREDITO: Estratégia com EDGE positivo. Win rate acima do break-even.")
elif win_rate >= 50:
    print("  🟡 VEREDITO: Estratégia NEUTRA. Win rate marginal, precisa de mais dados.")
else:
    print("  🔴 VEREDITO: Estratégia SEM EDGE nesta janela. Rever parâmetros.")
print("=" * 60)
