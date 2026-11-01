### Task 4: Фича `feat/limit-order-expiry` — TIF в ядре (отдельная ревью-ветка)

**Files:**
- Branch: `feat/limit-order-expiry` от HEAD `calibration-suite` (после Task 3)
- Modify: `strategylab/broker/orders.py` (+1 поле), `strategylab/broker/sim.py` (списание)
- Create: `tests/test_broker_expiry.py` (существующие брокерские тесты НЕ трогать)
- Create: `strategydocs/заметка_фича_expiry_лимиток_20261028.md`

**Interfaces:**
- Consumes: ничего из calib-артефактов — чистое ядро.
- Produces: `Order(side, kind, qty, limit_price, stop_price, reduce_only, expires_in_bars: int | None = None)` — седьмое поле со значением по умолчанию. Семантика: ордер подан после `apply_bar` с внутренним счётчиком `seq=S` → участвует в `apply_bar` S+1 … S+e → на входе первого `apply_bar` с `seq > S+e` молча списывается (без Fill). `expires_in_bars=None` — неограниченно (старое поведение, побайтовое). Внутренне: `_pending: list[_Rest]`, модульный `@dataclass(frozen=True, slots=True) class _Rest: order: Order; born: int`; счётчик — **поле slotted-датакласса** `_seq: int = field(default=0, repr=False)` (не ad-hoc атрибут: `BrokerSimulator` остаётся датаклассом, позиционный контракт `BrokerSimulator(costs, intrabar)` неизменен; A10(ii)); инкремент в начале `apply_bar`; `submit` пишет `born=self._seq`; все фильтры/циклы (`_cancel_reduced`, `_apply_stops`, `_apply_exits`, `_apply_limits`, `_apply_markets`) сохраняют объекты `_Rest`; свойство `pending_orders` наружу — прежний `list[Order]` (`[r.order for r in self._pending]`).

- [ ] **Step 1:** `git checkout -b feat/limit-order-expiry` (от HEAD calibration-suite).
- [ ] **Step 2: Падающие тесты** `tests/test_broker_expiry.py` (бары-синтетика в стиле `tests/test_broker_sim.py`; читаемый broker через `pending_orders`):
```python
def test_limit_expires_after_ttl_without_fill():   # low не касается; e=2: снят, pending пуст, fills нет
def test_limit_fills_before_expiry_maker():        # касание на S+1: Fill price==limit, fee==maker, reason "limit"
def test_expiry_none_rests_forever():              # 50 баров, pending жив
def test_expired_limit_does_not_fill_late():       # касание на S+e+1: НЕ исполняется
def test_position_untouched_by_expiry():           # ни pnl, ни позиции
def test_legacy_positional_construction():         # 6 позиционных аргументов валидны, expiry=None
```
- [ ] **Step 3:** `.venv\Scripts\python.exe -m pytest tests/test_broker_expiry.py -q` → FAIL.
- [ ] **Step 4: Реализовать** по Interfaces (orders.py — одно поле; sim.py — `_Rest`/`_seq`/фильтр списания в начале `apply_bar`: `self._pending = [r for r in self._pending if r.order.expires_in_bars is None or self._seq <= r.born + r.order.expires_in_bars]` до вызовов `_apply_*`).
- [ ] **Step 5:** pytest файла → PASS; **весь** `pytest tests -q` зелёный без единого изменённого assert в старых файлах; ruff; mypy.
- [ ] **Step 6: Записка Андрею** `strategydocs/заметка_фича_expiry_лимиток_20261028.md`: контекст (design §7, finding «maker-механику нельзя было проверить»), семантика (born/seq/окно жизни, молчаливое списание без Fill), совместимость (None-дефолт; старый набор тестов нетронутым зелёным; baseline-diff шага 7), объём diff, «мержить/отклонять — твоё решение; calibration-набор использует ветку merge'ом только для spec_04».
- [ ] **Step 7: Gate совместимости (baseline diff):** перепрогнать харнессом spec_02 (2 команды Task 2 Step 3) и spec_03 (2 команды Task 3 Step 3) с `--out data\calib\after_feat`; для каждого файла baseline (csv, journal p|o, report_p) сравнить `Get-FileHash` с одноимённым файлом after_feat — равен **хеш файла целиком**, не «мультимножество строк» (`Compare-Object` пропускает перестановки — A10(iii)). Любой несовпавший хеш → фича битая: merge НЕ делать, дефект в свод Task 9.
- [ ] **Step 8:** `git add -A; git commit -m "feat(broker): optional per-order TIF expiry for resting limit orders"`; `git push -u origin feat/limit-order-expiry`.
- [ ] **Step 9:** `git checkout calibration-suite; git merge --no-ff feat/limit-order-expiry -m "Merge feat/limit-order-expiry: kernel TIF for spec_04 runs"`; pytest ещё раз зелёный.

---

