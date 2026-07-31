import '../models/caregiver.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/api_page.dart';
import '../models/routine.dart';
import '../models/session_close.dart';
import '../models/stats.dart';

/// Demo 假資料的**初始內容**——後端端點尚未實作時，讓畫面能做完並看見真實排版。
///
/// 畫面不直接用這裡：一律走 `CareRepository`，由 `DemoRepository` 決定要不要拿這份
/// 初始資料。回傳型別與 `ApiClient` **完全相同**（`ApiPage<T>`、`DailyRoutineView`…），
/// 所以兩種資料來源對畫面而言沒有差別。
///
/// 資料內容照 docs/api.md 的欄位與 enum，日期一律相對今天產生，
/// 這樣 demo 當天不會出現「三個月前的紀錄」。
///
/// TODO: 正式資料齊備後整檔連同 `DemoRepository` 刪除。
abstract final class DemoData {
  /// 假的網路延遲：讓 loading 狀態真的會出現，不會一閃而過看不出有沒有做。
  static const latency = Duration(milliseconds: 400);

  static const elderId = 'eld_a1b2c3d4e5f6';

  static DateTime get _today {
    final n = DateTime.now();
    return DateTime(n.year, n.month, n.day);
  }

  static String _dateKey(DateTime d) =>
      '${d.year}-${_two(d.month)}-${_two(d.day)}';

  static String _two(int v) => v.toString().padLeft(2, '0');

  static Future<T> _delayed<T>(T value) => Future.delayed(latency, () => value);

  // ---- 呼叫者身分 ----

  /// 照護者自己的身分（`GET /me`）。
  ///
  /// [name] 留空：demo 的 ID token 只有 `sub`，沒有 `name` 也沒有 email 可取
  /// （見 `DemoAuthBackend._fakeIdToken`）。正式版由後端保證有值，畫面因此要能
  /// 接受沒有名字的情況——真正要給家人的是 ID，名字只是佐證。
  static Future<Caregiver> me({required String sub}) =>
      _delayed(Caregiver(caregiverId: caregiverIdFor(sub), name: ''));

  /// 由 Cognito `sub` 衍生 `cg_<8-lowercase-hex>`（api.md 的 ID 格式）。
  ///
  /// **必須是穩定的**：照護者把 ID 報給家人之後就不能再變，否則長輩那邊綁的是一組
  /// 對不上的值。所以這裡是純函式雜湊，不用隨機值、不做持久化。
  ///
  /// 用 mod 2^32 的多項式雜湊而不是 FNV-1a：FNV 的乘數會讓中間值超過 2^53，
  /// 在 web（JS number）上失去精度，同一個 sub 在不同平台會算出不同 ID。
  ///
  /// TODO: 後端上線後整檔刪除；正式版的 ID 由後端從 sub 衍生，App 不自己算。
  static String caregiverIdFor(String sub) {
    var h = 0;
    for (final c in sub.codeUnits) {
      h = (h * 131 + c) % 0x100000000;
    }
    return 'cg_${h.toRadixString(16).padLeft(8, '0')}';
  }

  // ---- 長者 ----

  /// 兩位長者：驗證照護者切換長輩的流程（api.md 的 `caregiver_ids` 支援多位）。
  static Future<ApiPage<Elder>> elders() => _delayed(ApiPage(items: [
        Elder(
          elderId: elderId,
          name: '陳阿蘭',
          nickname: '阿蘭嬤',
          birthYear: 1948,
          gender: 'female',
          langPreference: 'zh-TW',
          addressRegion: '台北市大安區',
          healthNotes: const ['高血壓', '膝關節退化'],
          family: const [
            FamilyMember(relation: '兒子', name: '陳志明', note: '在台北工作，每週三來訪'),
            FamilyMember(relation: '孫子', name: '小明', note: '高中生'),
          ],
          habitNote: '早睡早起，喜歡去公園散步、看歌仔戲',
          createdAt: _today.subtract(const Duration(days: 25)),
          updatedAt: _today.subtract(const Duration(days: 3)),
        ),
        Elder(
          elderId: 'eld_9f8e7d6c5b4a',
          name: '林金水',
          nickname: '阿水伯',
          birthYear: 1941,
          gender: 'male',
          langPreference: 'hak',
          addressRegion: '新竹縣竹東鎮',
          healthNotes: const ['糖尿病'],
          family: const [
            FamilyMember(relation: '女兒', name: '林淑芬', note: '同住'),
          ],
          habitNote: '習慣早上到廟口和朋友泡茶',
          createdAt: _today.subtract(const Duration(days: 12)),
          updatedAt: _today.subtract(const Duration(days: 12)),
        ),
      ]));

  // ---- 例行公事 ----

  static Future<List<Routine>> routines() => _delayed([
        Routine(
          routineId: 'rtn_001',
          elderId: elderId,
          title: '吃血壓藥',
          type: 'medication',
          schedule: const RoutineSchedule(freq: 'daily', time: '09:00'),
          createdBy: 'caregiver',
          createdAt: _today.subtract(const Duration(days: 25)),
        ),
        Routine(
          routineId: 'rtn_002',
          elderId: elderId,
          title: '量血壓',
          type: 'other',
          schedule: const RoutineSchedule(freq: 'daily', time: '19:00'),
          createdBy: 'caregiver',
          createdAt: _today.subtract(const Duration(days: 25)),
        ),
        Routine(
          routineId: 'rtn_003',
          elderId: elderId,
          title: '小明帶去看醫生',
          type: 'other',
          schedule: RoutineSchedule(
              freq: 'once', date: _dateKey(_today), time: '15:00'),
          createdBy: 'conversation',
          createdAt: _today.subtract(const Duration(days: 1)),
        ),
        Routine(
          routineId: 'rtn_004',
          elderId: elderId,
          title: '公園散步',
          type: 'activity',
          schedule:
              const RoutineSchedule(freq: 'weekly', weekday: 3, time: '16:00'),
          createdBy: 'caregiver',
          remind: false,
          createdAt: _today.subtract(const Duration(days: 20)),
        ),
      ]);

  /// 當日行程：三種狀態各一，好驗證 done／pending／missed 的視覺是否真的分得出來。
  static Future<DailyRoutineView> dailyRoutines(String date) =>
      _delayed(DailyRoutineView(
        date: date,
        items: [
          RoutineOccurrence(
            routineId: 'rtn_001',
            title: '吃血壓藥',
            type: 'medication',
            scheduledAt: _today.add(const Duration(hours: 9)),
            status: 'done',
            completedAt: _today.add(const Duration(hours: 9, minutes: 5)),
            completedBy: 'conversation',
          ),
          RoutineOccurrence(
            routineId: 'rtn_005',
            title: '吃早餐',
            type: 'diet',
            scheduledAt: _today.add(const Duration(hours: 7, minutes: 30)),
            status: 'missed',
          ),
          RoutineOccurrence(
            routineId: 'rtn_003',
            title: '小明帶去看醫生',
            type: 'other',
            scheduledAt: _today.add(const Duration(hours: 15)),
            status: 'pending',
          ),
          RoutineOccurrence(
            routineId: 'rtn_002',
            title: '量血壓',
            type: 'other',
            scheduledAt: _today.add(const Duration(hours: 19)),
            status: 'pending',
          ),
        ],
      ));

  /// 手動確認完成：回傳該筆的 done 狀態，`completed_by` 為 elder（長者自己按的）。
  static Future<RoutineOccurrence> completeRoutine(RoutineOccurrence o) =>
      _delayed(RoutineOccurrence(
        routineId: o.routineId,
        title: o.title,
        type: o.type,
        scheduledAt: o.scheduledAt,
        status: 'done',
        completedAt: DateTime.now(),
        completedBy: 'elder',
      ));

  // ---- 每日摘要 ----

  /// 今天那筆刻意給 partial（還有一段對話沒整理完），驗證提示有沒有做。
  static Future<ApiPage<DailySummary>> summaries() => _delayed(ApiPage(items: [
        DailySummary(
          elderId: elderId,
          date: _dateKey(_today),
          overview: '截至晚間八點，已處理資料顯示三餐正常並按時服藥；仍有一段對話等待批次整理。',
          sections: const SummarySections(
            diet: '三餐正常，早餐吃稀飯配醬瓜，午餐有魚有青菜。',
            activity: '下午到公園散步約 30 分鐘，遇到鄰居聊了一會。',
            sleep: '昨晚睡約七小時，半夜起來一次。',
            medication: '血壓藥已按時服用。',
            wellbeing: '提到膝蓋疼痛，心情平穩。',
            safety: '傍晚在浴室地板滑了一下，扶著把手沒有跌倒。',
            other: null,
          ),
          routines: const SummaryRoutines(completed: 1, missed: 1, items: [
            SummaryRoutineItem(
                routineId: 'rtn_001', title: '吃血壓藥', status: 'done'),
            SummaryRoutineItem(
                routineId: 'rtn_005', title: '吃早餐', status: 'missed'),
            SummaryRoutineItem(
                routineId: 'rtn_002', title: '量血壓', status: 'pending'),
          ]),
          // safety 事件會餵進 alerts（後端 ALERT_EVENT_TYPES），兩邊看得出是同一件事
          alerts: const ['傍晚在浴室差點滑倒', '今日多次提到膝蓋疼痛'],
          interactionCount: 6,
          dataStatus: SummaryDataStatus.partial,
          pendingSessionCount: 1,
          generatedAt: DateTime.now(),
        ),
        DailySummary(
          elderId: elderId,
          date: _dateKey(_today.subtract(const Duration(days: 1))),
          overview: '整日作息正常，服藥與量血壓都有完成，心情不錯。',
          sections: const SummarySections(
            diet: '三餐正常。',
            activity: '早上去市場買菜。',
            sleep: '睡滿八小時。',
            medication: '血壓藥、血壓量測都完成。',
            wellbeing: '心情不錯，提到孫子要來。',
            safety: null,
            other: '孫子小明週三會來訪。',
          ),
          routines: const SummaryRoutines(completed: 2, missed: 0, items: [
            SummaryRoutineItem(
                routineId: 'rtn_001', title: '吃血壓藥', status: 'done'),
            SummaryRoutineItem(
                routineId: 'rtn_002', title: '量血壓', status: 'done'),
          ]),
          interactionCount: 8,
          dataStatus: SummaryDataStatus.complete,
          generatedAt: _today.subtract(const Duration(hours: 4)),
        ),
        DailySummary(
          elderId: elderId,
          date: _dateKey(_today.subtract(const Duration(days: 2))),
          overview: '對話較少，晚間血壓未量測。',
          sections: const SummarySections(
            diet: '午餐吃得少。',
            activity: null,
            sleep: '約六小時，睡得不太安穩。',
            medication: '早上血壓藥有吃。',
            wellbeing: '說有點累，不太想出門。',
            safety: null,
            other: null,
          ),
          routines: const SummaryRoutines(completed: 1, missed: 1, items: [
            SummaryRoutineItem(
                routineId: 'rtn_001', title: '吃血壓藥', status: 'done'),
            SummaryRoutineItem(
                routineId: 'rtn_002', title: '量血壓', status: 'missed'),
          ]),
          alerts: const ['晚間血壓未量測', '提到疲倦、活動量下降'],
          interactionCount: 3,
          dataStatus: SummaryDataStatus.complete,
          generatedAt: _today.subtract(const Duration(days: 1, hours: 4)),
        ),
      ]));

  // ---- 生活事件 ----

  /// 第一頁帶 next_token，讓「載入更多」這條路真的走得到。
  static Future<ApiPage<LifeEvent>> events({String? nextToken}) {
    final second = nextToken != null;
    final base = _today.subtract(Duration(days: second ? 1 : 0));
    return _delayed(ApiPage(
      items: [
        LifeEvent(
          eventId: 'evt_${second ? 'b' : 'a'}1',
          elderId: elderId,
          ts: base.add(const Duration(hours: 19, minutes: 30)),
          type: 'wellbeing',
          detail: '提到膝蓋疼痛，走路時比較明顯，語氣略顯無奈。',
          source: 'conversation',
          conversationId: 'cnv_01J9',
        ),
        // safety：跌倒、走失、詐騙、居家危害等安全事件，realtime rail 當下就寫入
        LifeEvent(
          eventId: 'evt_${second ? 'b' : 'a'}7',
          elderId: elderId,
          ts: base.add(const Duration(hours: 18, minutes: 45)),
          type: 'safety',
          detail: '在浴室地板滑了一下，扶著把手沒有跌倒，說地上有積水。',
          source: 'conversation',
          conversationId: 'cnv_01J9',
        ),
        LifeEvent(
          eventId: 'evt_${second ? 'b' : 'a'}2',
          elderId: elderId,
          ts: base.add(const Duration(hours: 16, minutes: 10)),
          type: 'activity',
          detail: '到公園散步約 30 分鐘，遇到鄰居王太太聊天。',
          source: 'conversation',
          conversationId: 'cnv_01J8',
        ),
        LifeEvent(
          eventId: 'evt_${second ? 'b' : 'a'}3',
          elderId: elderId,
          ts: base.add(const Duration(hours: 12, minutes: 20)),
          type: 'diet',
          detail: '午餐吃了魚、青菜和半碗飯。',
          source: 'conversation',
          conversationId: 'cnv_01J7',
        ),
        LifeEvent(
          eventId: 'evt_${second ? 'b' : 'a'}4',
          elderId: elderId,
          ts: base.add(const Duration(hours: 9, minutes: 5)),
          type: 'medication',
          detail: '已服用血壓藥。',
          source: 'conversation',
          conversationId: 'cnv_01J6',
          routineId: 'rtn_001',
        ),
        LifeEvent(
          eventId: 'evt_${second ? 'b' : 'a'}5',
          elderId: elderId,
          ts: base.add(const Duration(hours: 8)),
          type: 'sleep',
          detail: '昨晚睡約七小時，半夜起來一次。',
          source: 'conversation',
          conversationId: 'cnv_01J5',
        ),
        LifeEvent(
          eventId: 'evt_${second ? 'b' : 'a'}6',
          elderId: elderId,
          ts: base.add(const Duration(hours: 7, minutes: 40)),
          type: 'other',
          detail: '照護者手動記錄：已預約下週三回診。',
          source: 'manual',
        ),
      ],
      // 只給一次下一頁，第二頁就到底。
      nextToken: second ? null : 'demo-cursor-2',
    ));
  }

  // ---- 統計 ----

  static Future<Stats> stats({int days = 7}) => _delayed(Stats(
        elderId: elderId,
        today: StatsToday(
          interactionCount: 6,
          lastInteractionAt:
              DateTime.now().subtract(const Duration(minutes: 42)),
        ),
        period: StatsPeriod(days: days, interactionCount: 35, activeDays: 6),
        byRoutine: const [
          RoutineStat(
              routineId: 'rtn_001', title: '吃血壓藥', completed: 7, total: 7),
          RoutineStat(
              routineId: 'rtn_002', title: '量血壓', completed: 5, total: 7),
          RoutineStat(
              routineId: 'rtn_004', title: '公園散步', completed: 1, total: 1),
          RoutineStat(
              routineId: 'rtn_005', title: '吃早餐', completed: 4, total: 7),
        ],
        daily: List.generate(days, (i) {
          final d = _today.subtract(Duration(days: days - 1 - i));
          const interactions = [4, 8, 3, 7, 5, 2, 6];
          const completed = [2, 3, 1, 3, 2, 1, 1];
          return DailyStat(
            date: _dateKey(d),
            interactionCount: interactions[i % interactions.length],
            routinesCompleted: completed[i % completed.length],
            routinesTotal: 3,
          );
        }),
      ));
}
