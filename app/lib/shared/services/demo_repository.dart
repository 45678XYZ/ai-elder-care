import '../models/api_page.dart';
import '../models/caregiver.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/routine.dart';
import '../models/stats.dart';
import 'care_repository.dart';
import 'demo_data.dart';

/// [CareRepository] 的假資料實作——`DemoData` 的固定資料，加上一層記憶體狀態。
///
/// **為什麼要有狀態**：`DemoData` 每次呼叫都重新生一份固定資料，於是新增行程、
/// 停用行程、改語言在畫面上看得到，重新整理就消失。畫面因此得自己把改動記在
/// `setState` 裡，而那套本地維護的邏輯接上真後端之後就是多餘的。
/// 這裡把改動留在記憶體，行為就跟真後端一致（寫入 → 重拉 → 還在），
/// 畫面兩邊共用同一套流程。
///
/// 狀態只活在這個 App process 裡，重開就回到初始資料——demo 前不必清任何東西。
///
/// TODO: 正式資料齊備後整檔連同 [DemoData] 移除。
class DemoRepository implements CareRepository {
  /// 第一次取用時從 [DemoData] 灌入，之後的寫入都改這一份。
  List<Elder>? _elders;
  List<Routine>? _routines;

  @override
  Future<Caregiver> me({required String sub}) => DemoData.me(sub: sub);

  // ---- 長者 ----

  @override
  Future<List<Elder>> elders() async {
    final cached = _elders;
    if (cached != null) return List.of(cached);
    final page = await DemoData.elders();
    _elders = page.items;
    return List.of(page.items);
  }

  @override
  Future<Elder> updateElder(String elderId, Map<String, dynamic> fields) async {
    if (_elders == null) await elders();
    final list = _elders!;
    final i = list.indexWhere((e) => e.elderId == elderId);
    if (i < 0) throw StateError('demo 資料裡沒有這位長者：$elderId');

    final updated = list[i].copyWith(
      name: fields['name'] as String?,
      nickname: fields['nickname'] as String?,
      langPreference: fields['lang_preference'] as String?,
      addressRegion: fields['address_region'] as String?,
      habitNote: fields['habit_note'] as String?,
      updatedAt: DateTime.now(),
    );
    list[i] = updated;
    return Future.delayed(DemoData.latency, () => updated);
  }

  // ---- 例行公事 ----

  @override
  Future<List<Routine>> routines({required String elderId}) async {
    final cached = _routines;
    if (cached != null) return List.of(cached);
    final list = await DemoData.routines();
    _routines = list;
    return List.of(list);
  }

  @override
  Future<Routine> createRoutine({
    required String clientRequestId,
    required String elderId,
    required Map<String, dynamic> fields,
  }) async {
    final list = await _mutableRoutines(elderId);
    // 真後端的 routine_id 由 elder_id + 呼叫者 + client_request_id 穩定衍生；
    // demo 取冪等鍵的前 8 碼就夠了——同一個鍵重送會落在同一筆（見下方 existing）。
    final routineId = 'rtn_${clientRequestId.substring(0, 8)}';
    final existing = list.indexWhere((r) => r.routineId == routineId);
    if (existing >= 0) return list[existing];

    final created = Routine(
      routineId: routineId,
      elderId: elderId,
      title: fields['title'] as String? ?? '',
      type: fields['type'] as String? ?? 'other',
      schedule: RoutineSchedule.fromJson(
          fields['schedule'] as Map<String, dynamic>? ?? const {}),
      remind: fields['remind'] as bool? ?? true,
      createdBy: 'caregiver',
      createdAt: DateTime.now(),
    );
    list.add(created);
    return Future.delayed(DemoData.latency, () => created);
  }

  @override
  Future<Routine> updateRoutine(
    String routineId, {
    required String clientRequestId,
    required Map<String, dynamic> fields,
  }) async {
    final list = await _mutableRoutines(null);
    final i = list.indexWhere((r) => r.routineId == routineId);
    if (i < 0) throw StateError('demo 資料裡沒有這筆例行公事：$routineId');

    final schedule = fields['schedule'];
    final updated = list[i].copyWith(
      title: fields['title'] as String?,
      type: fields['type'] as String?,
      schedule: schedule is Map<String, dynamic>
          ? RoutineSchedule.fromJson(schedule)
          : null,
      remind: fields['remind'] as bool?,
      active: fields['active'] as bool?,
    );
    list[i] = updated;
    return Future.delayed(DemoData.latency, () => updated);
  }

  @override
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  }) =>
      DemoData.dailyRoutines(date);

  @override
  Future<RoutineOccurrence> completeRoutine(
    RoutineOccurrence occurrence, {
    String? date,
  }) =>
      DemoData.completeRoutine(occurrence);

  /// 取可寫的行程清單（必要時先灌初始資料）。
  Future<List<Routine>> _mutableRoutines(String? elderId) async {
    if (_routines == null) {
      await routines(elderId: elderId ?? DemoData.elderId);
    }
    return _routines!;
  }

  // ---- 摘要、事件、統計（唯讀，直接轉給 DemoData）----

  @override
  Future<ApiPage<DailySummary>> summaries({
    required String elderId,
    String? from,
    String? to,
    String? nextToken,
  }) =>
      DemoData.summaries();

  @override
  Future<DailySummary> generateSummary({
    required String elderId,
    String? date,
  }) async {
    // 手動生成回單一物件（api.md）；demo 就回列表裡最新的那筆（今天）。
    final page = await DemoData.summaries();
    return page.items.first;
  }

  @override
  Future<ApiPage<LifeEvent>> events({
    required String elderId,
    String? from,
    String? to,
    String? type,
    String? nextToken,
  }) =>
      // type 不在這裡過濾：demo 的資料量小，時間軸畫面本來就在本地依分類篩選，
      // 兩邊都篩會讓「載入更多」拿到空頁。真後端才由 `type` 參數過濾。
      DemoData.events(nextToken: nextToken);

  @override
  Future<Stats> stats({required String elderId, int days = 7}) =>
      DemoData.stats(days: days);
}
