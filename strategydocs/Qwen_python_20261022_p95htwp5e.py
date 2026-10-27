import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt

# ==========================================
# 1. КОНФИГУРАЦИЯ СТРАТЕГИИ (НАСТРОЙКИ)
# ==========================================
TICKER = "BTC-USD"       # Инструмент для теста
PERIOD = "2y"            # Период истории (2 года)
INTERVAL = "1h"          # Таймфрейм (1h, 4h, 1d)

# Параметры стратегии "Зачистка ликвидности"
LOOKBACK = 20            # Период для определения границ диапазона (N свечей)
ATR_PERIOD = 14          # Период для расчета ATR
ATR_MULTIPLIER = 1.5     # Множитель ATR для расчета Стоп-Лосса
TP1_R_MULTIPLE = 1.5     # Частичная фиксация прибыли при 1.5R
TP2_R_MULTIPLE = 3.0     # Окончательная фиксация прибыли при 3.0R
RISK_PER_TRADE = 0.01    # Риск на сделку (1% от капитала, используется для симуляции)

# Начальный капитал для расчета кривой доходности
INITIAL_CAPITAL = 10000  

# ==========================================
# 2. ЗАГРУЗКА И ПОДГОТОВКА ДАННЫХ
# ==========================================
print(f"Загрузка данных {TICKER} за {PERIOD} (Таймфрейм: {INTERVAL})...")
df = yf.download(TICKER, period=PERIOD, interval=INTERVAL, progress=False)

# Очистка данных от дубликатов индекса (частая проблема yfinance)
df = df[~df.index.duplicated(keep='first')]
df.dropna(inplace=True)

# ==========================================
# 3. РАСЧЕТ ИНДИКАТОРОВ
# ==========================================
# Границы диапазона (Rolling High/Low)
df['Rolling_High'] = df['High'].rolling(window=LOOKBACK).max()
df['Rolling_Low'] = df['Low'].rolling(window=LOOKBACK).min()

# ATR (Average True Range)
df['Prev_Close'] = df['Close'].shift(1)
df['TR'] = np.maximum(
    df['High'] - df['Low'],
    np.maximum(
        abs(df['High'] - df['Prev_Close']),
        abs(df['Low'] - df['Prev_Close'])
    )
)
df['ATR'] = df['TR'].rolling(window=ATR_PERIOD).mean()

# ==========================================
# 4. ЛОГИКА СИГНАЛОВ (ВХОД)
# ==========================================
# Условие для ЛОНГА: Цена пробила минимум диапазона, но закрылась выше него (фитиль)
# Это имитирует сбор ликвидности (stop hunt) и возврат в диапазон.
df['Sweep_Low'] = (df['Low'] < df['Rolling_Low'].shift(1)) & (df['Close'] > df['Rolling_Low'].shift(1))

# Фильтр: не входим, если ATR не рассчитан (начало графика)
df['Valid_Signal'] = df['Sweep_Low'] & (~df['ATR'].isna())

# ==========================================
# 5. ДВИЖОК БЭКТЕСТА (СИМУЛЯЦИЯ СДЕЛОК)
# ==========================================
print("Запуск симуляции торговых сделок...")

trades = []
capital = INITIAL_CAPITAL
equity_curve = [INITIAL_CAPITAL]

# Переменные состояния
in_position = False
entry_price = 0
sl_price = 0
tp1_price = 0
tp2_price = 0
position_size = 0
tp1_hit = False

for i in range(LOOKBACK + 2, len(df)):
    row = df.iloc[i]
    prev_row = df.iloc[i-1]
    
    # Если мы не в позиции, проверяем сигнал на вход
    if not in_position and row['Valid_Signal']:
        in_position = True
        entry_price = row['Close']
        
        # Расчет Стоп-Лосса и Тейк-Профитов на основе R (расстояния до стопа)
        risk_distance = entry_price - (row['Low'] - (row['ATR'] * ATR_MULTIPLIER))
        sl_price = entry_price - risk_distance
        tp1_price = entry_price + (risk_distance * TP1_R_MULTIPLE)
        tp2_price = entry_price + (risk_distance * TP2_R_MULTIPLE)
        
        # Размер позиции рассчитывается так, чтобы при ударе по SL мы потеряли RISK_PER_TRADE от капитала
        position_size = (capital * RISK_PER_TRADE) / risk_distance
        tp1_hit = False
        
    # Если мы в позиции, проверяем условия выхода
    elif in_position:
        # Приоритет: Сначала проверяем Стоп-Лосс (консервативный подход)
        if row['Low'] <= sl_price:
            # Сделка закрыта по стопу
            loss = position_size * (sl_price - entry_price)
            capital += loss
            trades.append({'Type': 'Loss', 'PnL': loss, 'R_Multiple': -1.0})
            in_position = False
            
        # Проверяем TP2 (если TP1 еще не был достигнут, мы могли проскочить сразу ко второму)
        elif row['High'] >= tp2_price:
            profit = position_size * (tp2_price - entry_price)
            capital += profit
            trades.append({'Type': 'Win_TP2', 'PnL': profit, 'R_Multiple': TP2_R_MULTIPLE})
            in_position = False
            
        # Проверяем TP1
        elif not tp1_hit and row['High'] >= tp1_price:
            tp1_hit = True
            # Закрываем 50% позиции по TP1
            partial_profit = (position_size * 0.5) * (tp1_price - entry_price)
            capital += partial_profit
            # Переводим Стоп-Лосс в безубыток (на цену входа) для оставшихся 50%
            sl_price = entry_price 
            
        # Если TP1 уже был, проверяем выход оставшейся части по трейлинг-стопу (безубыток) или TP2
        elif tp1_hit:
            if row['High'] >= tp2_price:
                profit = (position_size * 0.5) * (tp2_price - entry_price)
                capital += profit
                trades.append({'Type': 'Win_TP2_After_TP1', 'PnL': partial_profit + profit, 'R_Multiple': (TP1_R_MULTIPLE * 0.5) + (TP2_R_MULTIPLE * 0.5)})
                in_position = False
            elif row['Low'] <= sl_price: # sl_price теперь равен entry_price (безубыток)
                trades.append({'Type': 'Breakeven', 'PnL': partial_profit, 'R_Multiple': TP1_R_MULTIPLE * 0.5})
                in_position = False

    equity_curve.append(capital)

# ==========================================
# 6. АНАЛИЗ РЕЗУЛЬТАТОВ (МАТЕМАТИКА)
# ==========================================
df_trades = pd.DataFrame(trades)

if len(df_trades) == 0:
    print("Сделки не были совершены. Проверьте параметры или таймфрейм.")
else:
    total_trades = len(df_trades)
    winning_trades = df_trades[df_trades['PnL'] > 0]
    losing_trades = df_trades[df_trades['PnL'] < 0]
    
    win_rate = len(winning_trades) / total_trades
    avg_win = winning_trades['PnL'].mean() if len(winning_trades) > 0 else 0
    avg_loss = abs(losing_trades['PnL'].mean()) if len(losing_trades) > 0 else 0
    
    gross_profit = winning_trades['PnL'].sum()
    gross_loss = abs(losing_trades['PnL'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    # Математическое ожидание на сделку (в % от начального капитала)
    expectancy = (win_rate * (avg_win / INITIAL_CAPITAL)) - ((1 - win_rate) * (avg_loss / INITIAL_CAPITAL))
    
    # Максимальная просадка (Max Drawdown)
    equity_series = pd.Series(equity_curve)
    rolling_max = equity_series.cummax()
    drawdown = (equity_series - rolling_max) / rolling_max * 100
    max_drawdown = abs(drawdown.min())

    print("\n" + "="*50)
    print("📊 РЕЗУЛЬТАТЫ БЭКТЕСТА")
    print("="*50)
    print(f"Всего сделок:          {total_trades}")
    print(f"Win Rate:              {win_rate:.2%}")
    print(f"Средний выигрыш (AvgWin): ${avg_win:.2f}")
    print(f"Средний проигрыш (AvgLoss): ${avg_loss:.2f}")
    print(f"Профит-фактор (PF):    {profit_factor:.2f}")
    print(f"Мат. ожидание (Expectancy): {expectancy:.4%} от капитала на сделку")
    print(f"Макс. просадка (MDD):  {max_drawdown:.2f}%")
    print(f"Итоговый капитал:      ${capital:.2f} (Доходность: {(capital/INITIAL_CAPITAL - 1):.2%})")
    print("="*50 + "\n")

    # ==========================================
    # 7. ВИЗУАЛИЗАЦИЯ
    # ==========================================
    plt.figure(figsize=(12, 6))
    plt.plot(equity_curve, label='Equity Curve', color='blue')
    plt.title(f'Backtest Equity Curve: {TICKER} ({INTERVAL})')
    plt.xlabel('Trades / Time')
    plt.ylabel('Capital ($)')
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.show()