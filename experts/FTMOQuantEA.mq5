//+------------------------------------------------------------------+
//|                                               FTMOQuantEA.mq5     |
//|  Donchian/EMA trend EA with account-level FTMO 2-Step controls.  |
//|  No strategy or EA can guarantee a challenge pass.               |
//+------------------------------------------------------------------+
#property copyright "Bot-EA contributors"
#property version   "1.00"
#property strict
#property description "Conservative trend breakout EA with FTMO 2-Step risk guards"

#include <Trade/Trade.mqh>

input group "Strategy"
input ENUM_TIMEFRAMES InpSignalTimeframe       = PERIOD_H1;
input int             InpDonchianLookback      = 20;
input int             InpFastEmaPeriod         = 50;
input int             InpSlowEmaPeriod         = 200;
input int             InpAtrPeriod             = 14;
input double          InpStopAtrMultiple       = 2.0;
input double          InpRewardRisk            = 2.2;
input bool            InpAllowLong             = true;
input bool            InpAllowShort            = true;

input group "Position risk"
input double          InpRiskPerTradePct       = 0.35;
input int             InpMaxTradesPerDay       = 2;
input int             InpMaxLosingTradesPerDay = 2;
input double          InpMaxSpreadPoints       = 25.0;
input long            InpMagicNumber           = 26071401;
input int             InpSlippagePoints        = 10;

input group "FTMO 2-Step account guard"
input double          InpChallengeInitialBalance = 0.0;
input double          InpOfficialDailyLossPct    = 5.0;
input double          InpOfficialTotalLossPct    = 10.0;
input double          InpSoftDailyLossPct        = 4.0;
input double          InpSoftTotalLossPct        = 8.0;
input double          InpDailyProfitLockPct       = 1.0;
input int             InpDailyResetHourServer    = 0;
input int             InpDailyResetMinuteServer  = 0;
input bool            InpEmergencyCloseAllAccountPositions = true;
input string          InpStateId                  = "challenge1";

input group "Trading window (server time)"
input int             InpSessionStartHour      = 7;
input int             InpSessionEndHour        = 20;
input bool            InpCloseBeforeWeekend    = true;
input int             InpFridayCloseHour       = 20;

input group "Trade management"
input double          InpBreakEvenAtR          = 1.0;
input double          InpTrailStartAtR         = 1.5;
input double          InpTrailAtrMultiple      = 2.0;

CTrade trade;
int    fastEmaHandle = INVALID_HANDLE;
int    slowEmaHandle = INVALID_HANDLE;
int    atrHandle     = INVALID_HANDLE;
double initialBalance = 0.0;
double dayStartBalance = 0.0;
int    currentTradingDay = 0;
bool   tradingLocked = false;
bool   emergencyMode = false;
datetime lastSignalBar = 0;
string statePrefix;

//+------------------------------------------------------------------+
//| Persistent state helpers                                         |
//+------------------------------------------------------------------+
string StateKey(const string suffix)
{
   return statePrefix + suffix;
}

int TradingDayId(const datetime now)
{
   datetime shifted = now - InpDailyResetHourServer * 3600
                           - InpDailyResetMinuteServer * 60;
   MqlDateTime parts;
   TimeToStruct(shifted, parts);
   return parts.year * 10000 + parts.mon * 100 + parts.day;
}

datetime TradingDayStart(const datetime now)
{
   MqlDateTime parts;
   TimeToStruct(now, parts);
   parts.hour = InpDailyResetHourServer;
   parts.min  = InpDailyResetMinuteServer;
   parts.sec  = 0;
   datetime start = StructToTime(parts);
   if(now < start)
      start -= 86400;
   return start;
}

void RefreshDailyState()
{
   datetime now = TimeTradeServer();
   int dayId = TradingDayId(now);
   if(dayId == currentTradingDay)
      return;

   currentTradingDay = dayId;
   // Reconstruct the balance at reset even when the first chart tick is late.
   datetime start = TradingDayStart(now);
   double balanceDelta = 0.0;
   if(HistorySelect(start, now))
   {
      int deals = HistoryDealsTotal();
      for(int i = 0; i < deals; ++i)
      {
         ulong ticket = HistoryDealGetTicket(i);
         balanceDelta += HistoryDealGetDouble(ticket, DEAL_PROFIT);
         balanceDelta += HistoryDealGetDouble(ticket, DEAL_SWAP);
         balanceDelta += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
         balanceDelta += HistoryDealGetDouble(ticket, DEAL_FEE);
      }
   }
   dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE) - balanceDelta;
   tradingLocked = false;
   if(emergencyMode)
      tradingLocked = true;
   GlobalVariableSet(StateKey("day_id"), (double)currentTradingDay);
   GlobalVariableSet(StateKey("day_balance"), dayStartBalance);
   GlobalVariableSet(StateKey("lock_day"),
                     tradingLocked ? (double)currentTradingDay : 0.0);
}

void LoadState()
{
   statePrefix = "FTMOQ_" + StringFormat("%I64d", AccountInfoInteger(ACCOUNT_LOGIN))
               + "_" + InpStateId + "_";

   if(InpChallengeInitialBalance > 0.0)
   {
      initialBalance = InpChallengeInitialBalance;
      GlobalVariableSet(StateKey("initial"), initialBalance);
   }
   else if(GlobalVariableCheck(StateKey("initial")))
      initialBalance = GlobalVariableGet(StateKey("initial"));
   else
   {
      initialBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      GlobalVariableSet(StateKey("initial"), initialBalance);
   }

   if(GlobalVariableCheck(StateKey("emergency_active")) &&
      GlobalVariableGet(StateKey("emergency_active")) > 0.0)
      emergencyMode = true;

   int today = TradingDayId(TimeTradeServer());
   if(GlobalVariableCheck(StateKey("day_id")) &&
      (int)GlobalVariableGet(StateKey("day_id")) == today &&
      GlobalVariableCheck(StateKey("day_balance")))
   {
      currentTradingDay = today;
      dayStartBalance = GlobalVariableGet(StateKey("day_balance"));
      if(GlobalVariableCheck(StateKey("lock_day")) &&
         (int)GlobalVariableGet(StateKey("lock_day")) == today)
         tradingLocked = true;
      if(emergencyMode)
         tradingLocked = true;
   }
   else
   {
      currentTradingDay = 0;
      RefreshDailyState();
   }
}

//+------------------------------------------------------------------+
//| Account compliance                                               |
//+------------------------------------------------------------------+
double DailyOfficialFloor()
{
   return dayStartBalance - initialBalance * InpOfficialDailyLossPct / 100.0;
}

double TotalOfficialFloor()
{
   return initialBalance * (1.0 - InpOfficialTotalLossPct / 100.0);
}

double DailySoftFloor()
{
   return dayStartBalance - initialBalance * InpSoftDailyLossPct / 100.0;
}

double TotalSoftFloor()
{
   return initialBalance * (1.0 - InpSoftTotalLossPct / 100.0);
}

double ActiveProtectionFloor()
{
   return MathMax(MathMax(DailyOfficialFloor(), TotalOfficialFloor()),
                  MathMax(DailySoftFloor(), TotalSoftFloor()));
}

double TodayClosedPnl()
{
   datetime start = TradingDayStart(TimeTradeServer());
   if(!HistorySelect(start, TimeTradeServer()))
      return 0.0;

   double pnl = 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; ++i)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;
      pnl += HistoryDealGetDouble(ticket, DEAL_PROFIT);
      pnl += HistoryDealGetDouble(ticket, DEAL_SWAP);
      pnl += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      pnl += HistoryDealGetDouble(ticket, DEAL_FEE);
   }
   return pnl;
}

void TodayStrategyStats(int &entries, int &losses)
{
   entries = 0;
   losses = 0;
   datetime start = TradingDayStart(TimeTradeServer());
   if(!HistorySelect(start, TimeTradeServer()))
      return;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; ++i)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0 ||
         HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber)
         continue;

      ENUM_DEAL_ENTRY entry =
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN || entry == DEAL_ENTRY_INOUT)
         entries++;

      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY ||
         entry == DEAL_ENTRY_INOUT)
      {
         double pnl = HistoryDealGetDouble(ticket, DEAL_PROFIT)
                    + HistoryDealGetDouble(ticket, DEAL_SWAP)
                    + HistoryDealGetDouble(ticket, DEAL_COMMISSION)
                    + HistoryDealGetDouble(ticket, DEAL_FEE);
         if(pnl < 0.0)
            losses++;
      }
   }
}

void EmergencyFlatten(const string reason)
{
   tradingLocked = true;
   emergencyMode = true;
   GlobalVariableSet(StateKey("lock_day"), (double)currentTradingDay);
   GlobalVariableSet(StateKey("emergency_active"), 1.0);
   Print("FTMO risk lock: ", reason, ". Retrying liquidation until flat.");
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(InpEmergencyCloseAllAccountPositions || magic == InpMagicNumber)
      {
         bool requested = trade.PositionClose(ticket);
         uint retcode = trade.ResultRetcode();
         if(!requested ||
            (retcode != TRADE_RETCODE_DONE &&
             retcode != TRADE_RETCODE_DONE_PARTIAL))
            Print("Emergency close failed for #", ticket, ": ",
                  trade.ResultRetcodeDescription());
      }
   }

   bool targetsRemain = false;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 &&
         (InpEmergencyCloseAllAccountPositions ||
          PositionGetInteger(POSITION_MAGIC) == InpMagicNumber))
      {
         targetsRemain = true;
         break;
      }
   }
   emergencyMode = targetsRemain;
   GlobalVariableSet(StateKey("emergency_active"), targetsRemain ? 1.0 : 0.0);
}

bool CheckAccountGuard()
{
   RefreshDailyState();
   if(emergencyMode)
   {
      EmergencyFlatten("protection mode remains active");
      return false;
   }
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double floor = ActiveProtectionFloor();

   if(equity <= floor)
   {
      EmergencyFlatten("equity reached the configured protection floor");
      return false;
   }

   // A modest daily gain lock reduces give-back and Best Day concentration.
   if(InpDailyProfitLockPct > 0.0 &&
      TodayClosedPnl() >= initialBalance * InpDailyProfitLockPct / 100.0)
   {
      tradingLocked = true;
      GlobalVariableSet(StateKey("lock_day"), (double)currentTradingDay);
      return false;
   }
   return !tradingLocked;
}

bool ProjectedRiskAllowed(const double riskMoney)
{
   double projectedEquity = AccountInfoDouble(ACCOUNT_EQUITY) - riskMoney * 1.15;
   return projectedEquity > ActiveProtectionFloor();
}

//+------------------------------------------------------------------+
//| Strategy helpers                                                 |
//+------------------------------------------------------------------+
double IndicatorValue(const int handle, const int shift)
{
   double value[1];
   if(CopyBuffer(handle, 0, shift, 1, value) != 1)
      return EMPTY_VALUE;
   return value[0];
}

bool IsTradingSession()
{
   MqlDateTime now;
   TimeToStruct(TimeTradeServer(), now);
   if(now.day_of_week == 0 || now.day_of_week == 6)
      return false;
   if(now.day_of_week == 5 && now.hour >= InpFridayCloseHour)
      return false;

   if(InpSessionStartHour == InpSessionEndHour)
      return true;
   if(InpSessionStartHour < InpSessionEndHour)
      return now.hour >= InpSessionStartHour && now.hour < InpSessionEndHour;
   return now.hour >= InpSessionStartHour || now.hour < InpSessionEndHour;
}

bool HasOpenSymbolPosition()
{
   return PositionSelect(_Symbol);
}

int Signal()
{
   if(Bars(_Symbol, InpSignalTimeframe) <
      InpSlowEmaPeriod + InpDonchianLookback + 10)
      return 0;

   double fast1 = IndicatorValue(fastEmaHandle, 1);
   double fast2 = IndicatorValue(fastEmaHandle, 2);
   double slow1 = IndicatorValue(slowEmaHandle, 1);
   if(fast1 == EMPTY_VALUE || fast2 == EMPTY_VALUE || slow1 == EMPTY_VALUE)
      return 0;

   double upper = -DBL_MAX;
   double lower = DBL_MAX;
   for(int shift = 2; shift < InpDonchianLookback + 2; ++shift)
   {
      upper = MathMax(upper, iHigh(_Symbol, InpSignalTimeframe, shift));
      lower = MathMin(lower, iLow(_Symbol, InpSignalTimeframe, shift));
   }

   double close1 = iClose(_Symbol, InpSignalTimeframe, 1);
   if(InpAllowLong && close1 > upper && fast1 > slow1 && fast1 > fast2)
      return 1;
   if(InpAllowShort && close1 < lower && fast1 < slow1 && fast1 < fast2)
      return -1;
   return 0;
}

double PositionSize(const int direction, const double entryPrice,
                    const double stopPrice, const double riskMoney)
{
   double lossPerLot = 0.0;
   ENUM_ORDER_TYPE orderType = direction > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcProfit(orderType, _Symbol, 1.0, entryPrice, stopPrice,
                       lossPerLot) ||
      lossPerLot >= 0.0)
      return 0.0;

   // Leave part of the risk budget for commission, slippage and gaps.
   double volume = (riskMoney / 1.10) / MathAbs(lossPerLot);
   double minVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      return 0.0;

   volume = MathFloor(volume / step) * step;
   volume = MathMin(volume, maxVolume);
   if(volume < minVolume)
      return 0.0;
   return NormalizeDouble(volume, 8);
}

bool OpenTrade(const int direction)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   double spreadPoints = (tick.ask - tick.bid) / _Point;
   if(spreadPoints > InpMaxSpreadPoints)
      return false;

   double atr = IndicatorValue(atrHandle, 1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return false;

   long stopsLevelPoints = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double stopDistance = MathMax(atr * InpStopAtrMultiple,
                                 (stopsLevelPoints + 2) * _Point);
   double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY)
                    * InpRiskPerTradePct / 100.0;
   if(!ProjectedRiskAllowed(riskMoney))
      return false;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   bool sent = false;
   if(direction > 0)
   {
      double sl = NormalizeDouble(tick.bid - stopDistance, digits);
      double actualRisk = tick.ask - sl;
      double tp = NormalizeDouble(tick.ask + actualRisk * InpRewardRisk, digits);
      double volume = PositionSize(direction, tick.ask, sl, riskMoney);
      if(volume <= 0.0)
         return false;
      sent = trade.Buy(volume, _Symbol, 0.0, sl, tp, "FTMOQ breakout");
   }
   else
   {
      double sl = NormalizeDouble(tick.ask + stopDistance, digits);
      double actualRisk = sl - tick.bid;
      double tp = NormalizeDouble(tick.bid - actualRisk * InpRewardRisk, digits);
      double volume = PositionSize(direction, tick.bid, sl, riskMoney);
      if(volume <= 0.0)
         return false;
      sent = trade.Sell(volume, _Symbol, 0.0, sl, tp, "FTMOQ breakout");
   }

   uint retcode = trade.ResultRetcode();
   if(!sent || (retcode != TRADE_RETCODE_DONE &&
                retcode != TRADE_RETCODE_DONE_PARTIAL &&
                retcode != TRADE_RETCODE_PLACED))
   {
      Print("Order failed: ", trade.ResultRetcodeDescription());
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Open-position management                                         |
//+------------------------------------------------------------------+
void ManagePositions()
{
   double atr = IndicatorValue(atrHandle, 1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long stopLevelPoints = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStopDistance = (stopLevelPoints + 2) * _Point;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      ENUM_POSITION_TYPE type =
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      if(sl <= 0.0)
         continue;

      // Before break-even, the original SL still encodes initial risk.
      double initialRisk = MathAbs(open - sl);
      string riskKey = StateKey("risk_" + StringFormat("%I64d", ticket));
      if(GlobalVariableCheck(riskKey))
         initialRisk = GlobalVariableGet(riskKey);
      else
         GlobalVariableSet(riskKey, initialRisk);
      if(initialRisk <= 0.0)
         continue;

      double move = type == POSITION_TYPE_BUY ? tick.bid - open : open - tick.ask;
      double candidate = sl;

      if(move >= initialRisk * InpBreakEvenAtR)
      {
         if(type == POSITION_TYPE_BUY)
            candidate = MathMax(candidate, open);
         else
            candidate = MathMin(candidate, open);
      }

      if(move >= initialRisk * InpTrailStartAtR)
      {
         if(type == POSITION_TYPE_BUY)
            candidate = MathMax(candidate, tick.bid - atr * InpTrailAtrMultiple);
         else
            candidate = MathMin(candidate, tick.ask + atr * InpTrailAtrMultiple);
      }

      if(type == POSITION_TYPE_BUY)
         candidate = MathMin(candidate, tick.bid - minStopDistance);
      else
         candidate = MathMax(candidate, tick.ask + minStopDistance);
      candidate = NormalizeDouble(candidate, digits);

      bool improves = type == POSITION_TYPE_BUY
                      ? candidate > sl + _Point
                      : candidate < sl - _Point;
      if(improves)
      {
         bool requested = trade.PositionModify(ticket, candidate, tp);
         uint retcode = trade.ResultRetcode();
         if(!requested ||
            (retcode != TRADE_RETCODE_DONE &&
             retcode != TRADE_RETCODE_NO_CHANGES))
            Print("Stop update failed for #", ticket, ": ",
                  trade.ResultRetcodeDescription());
      }
   }
}

void HandleWeekendExit()
{
   if(!InpCloseBeforeWeekend)
      return;
   MqlDateTime now;
   TimeToStruct(TimeTradeServer(), now);
   if(now.day_of_week != 5 || now.hour < InpFridayCloseHour)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         bool requested = trade.PositionClose(ticket);
         uint retcode = trade.ResultRetcode();
         if(!requested ||
            (retcode != TRADE_RETCODE_DONE &&
             retcode != TRADE_RETCODE_DONE_PARTIAL))
            Print("Weekend close failed for #", ticket, ": ",
                  trade.ResultRetcodeDescription());
      }
   }
}

//+------------------------------------------------------------------+
//| Expert lifecycle                                                 |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpDonchianLookback < 2 || InpFastEmaPeriod < 2 ||
      InpSlowEmaPeriod <= InpFastEmaPeriod || InpAtrPeriod < 2 ||
      InpRiskPerTradePct <= 0.0 || InpStopAtrMultiple <= 0.0 ||
      InpRewardRisk <= 0.0 || InpMaxTradesPerDay < 1 ||
      InpMaxLosingTradesPerDay < 1 || InpMaxSpreadPoints < 0.0 ||
      InpSlippagePoints < 0 || InpBreakEvenAtR <= 0.0 ||
      InpTrailStartAtR < InpBreakEvenAtR || InpTrailAtrMultiple <= 0.0 ||
      InpSoftDailyLossPct <= 0.0 ||
      InpSoftDailyLossPct >= InpOfficialDailyLossPct ||
      InpSoftTotalLossPct <= 0.0 ||
      InpSoftTotalLossPct >= InpOfficialTotalLossPct ||
      InpDailyResetHourServer < 0 || InpDailyResetHourServer > 23 ||
      InpDailyResetMinuteServer < 0 || InpDailyResetMinuteServer > 59 ||
      InpSessionStartHour < 0 || InpSessionStartHour > 23 ||
      InpSessionEndHour < 0 || InpSessionEndHour > 23 ||
      InpFridayCloseHour < 0 || InpFridayCloseHour > 23 ||
      StringLen(InpStateId) < 1 || StringLen(InpStateId) > 20)
   {
      Print("Invalid EA inputs");
      return INIT_PARAMETERS_INCORRECT;
   }

   fastEmaHandle = iMA(_Symbol, InpSignalTimeframe, InpFastEmaPeriod,
                       0, MODE_EMA, PRICE_CLOSE);
   slowEmaHandle = iMA(_Symbol, InpSignalTimeframe, InpSlowEmaPeriod,
                       0, MODE_EMA, PRICE_CLOSE);
   atrHandle = iATR(_Symbol, InpSignalTimeframe, InpAtrPeriod);
   if(fastEmaHandle == INVALID_HANDLE ||
      slowEmaHandle == INVALID_HANDLE ||
      atrHandle == INVALID_HANDLE)
      return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);
   LoadState();
   EventSetTimer(1);

   Print("FTMOQuantEA initialized. Initial balance=", initialBalance,
         ", daily baseline=", dayStartBalance,
         ". Confirm reset time matches 00:00 CE(S)T.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(fastEmaHandle != INVALID_HANDLE)
      IndicatorRelease(fastEmaHandle);
   if(slowEmaHandle != INVALID_HANDLE)
      IndicatorRelease(slowEmaHandle);
   if(atrHandle != INVALID_HANDLE)
      IndicatorRelease(atrHandle);
}

void OnTimer()
{
   // Account-level limits must still be watched when this chart is quiet.
   CheckAccountGuard();
   HandleWeekendExit();
}

void OnTick()
{
   bool accountSafe = CheckAccountGuard();
   HandleWeekendExit();
   ManagePositions();
   if(!accountSafe || !IsTradingSession() || HasOpenSymbolPosition())
      return;

   int entries, losses;
   TodayStrategyStats(entries, losses);
   if(entries >= InpMaxTradesPerDay || losses >= InpMaxLosingTradesPerDay)
      return;

   datetime bar = iTime(_Symbol, InpSignalTimeframe, 0);
   if(bar <= 0 || bar == lastSignalBar)
      return;
   lastSignalBar = bar;

   int signal = Signal();
   if(signal != 0)
      OpenTrade(signal);
}
