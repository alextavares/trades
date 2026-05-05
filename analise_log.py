with open('bot_real.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()

seen = set()
total = rsi_alto = banda_true = sinais = mercados = 0
max_rsi = 0
max_rsi_time = ''
ordem_enviada = False

for l in lines:
    if 'BTC:' in l and l not in seen:
        seen.add(l)
        total += 1
        if 'Banda: True' in l:
            banda_true += 1
        try:
            rsi_val = float(l.split('RSI:')[1].split('|')[0].strip())
            if rsi_val > 70:
                rsi_alto += 1
            if rsi_val > max_rsi:
                max_rsi = rsi_val
                max_rsi_time = l.split(']')[0].replace('[','').strip()
        except:
            pass
    if 'SINAL REAL' in l and l not in seen:
        seen.add(l)
        sinais += 1
    if 'Mercado Encontrado' in l and l not in seen:
        seen.add(l)
        mercados += 1
    if 'ORDEM ENVIADA' in l or 'order' in l.lower():
        ordem_enviada = True

print('=== RESUMO ANALÍTICO DO ROBÔ REAL (Últimas ~7 horas) ===')
print()
print(f'  Ciclos monitorados (5min cada):  {total}')
print(f'  Ciclos com RSI > 70:             {rsi_alto}')
print(f'  Ciclos com Banda Superior=True:  {banda_true}')
print(f'  Sinais disparados (RSI+Banda):   {sinais}')
print(f'  Mercados encontrados:            {mercados}')
print(f'  Ordens confirmadas enviadas:     {"SIM" if ordem_enviada else "NÃO"}')
print(f'  RSI máximo atingido:             {max_rsi:.2f} em {max_rsi_time}')
print()
print('=== DIAGNÓSTICO ===')
print()

if sinais == 1 and mercados == 1 and not ordem_enviada:
    print('  ⚠️  PROBLEMA IDENTIFICADO:')
    print('  O robô disparou sinal às 04:55 e achou o mercado, MAS a ordem')
    print('  não foi confirmada no log ("ORDEM ENVIADA" não aparece).')
    print()
    print('  Causa provável: Erro silencioso ao enviar a ordem via CLOB API.')
    print('  Pode ser problema de saldo, credenciais expiradas, ou exception')
    print('  que foi engolida sem aparecer no log.')
    print()

print('  📊 CONTEXTO DO MERCADO:')
print(f'  Das 7 horas monitoradas, RSI > 70 ocorreu em apenas {rsi_alto} ciclos.')
print(f'  A Banda Superior foi tocada em apenas {banda_true} ciclos.')
print(f'  Os dois aconteceram JUNTOS em: {sinais} ciclo(s) - muito raro.')
print()
print('  ➡  A estratégia de Reversão (atual) opera em ~14% do tempo.')
print('  ➡  A estratégia de Continuação (observador) operaria 100% do tempo.')
