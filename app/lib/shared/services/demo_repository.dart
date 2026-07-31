import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../models/api_page.dart';
import '../models/caregiver.dart';
import '../models/chat_reply.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/routine.dart';
import '../models/stats.dart';
import 'api_client.dart';
import 'api_error_codes.dart';
import 'api_exception.dart';
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
/// 唯一的例外是已連結的家人（見 [_caregiversByElder]），那在 demo 流程裡是一次性設定。
///
/// TODO: 正式資料齊備後整檔連同 [DemoData] 移除。
class DemoRepository implements CareRepository {
  /// [api] 只有 [chat] 用得到（打本機 RAG PoC 取回覆文字）；測試可注入假的 HTTP client。
  DemoRepository({ApiClient? api}) : _api = api ?? ApiClient();

  /// 第一次取用時從 [DemoData] 灌入，之後的寫入都改這一份。
  List<Elder>? _elders;
  List<Routine>? _routines;
  List<RoutineOccurrence>? _occurrences;

  /// [_occurrences] 是哪一天的。跨日時整份換掉。
  String? _occurrenceDate;

  /// demo 模式下回覆內容仍然來自本機 RAG PoC 的 `/ask`——那是真的模型回答，
  /// 換成罐頭字串等於把 demo 最有價值的一段變成假的。
  final ApiClient _api;

  // ---- 對話 ----

  /// 回覆文字來自本機 RAG PoC 的 `/ask`（真的模型回答）；`routines_updated` 則由
  /// **本地關鍵字比對**推出來，見 [_matchCompletedRoutine]。
  ///
  /// ⚠️ **這裡的行程完成判定不是 AI 判斷的。** 正式路徑是後端 Bedrock Agent tool
  /// calling 在回話之前就把完成寫進資料庫（api.md）；這條只是讓「講完話 → 今日畫面
  /// 自己打勾」那段 App 端的路能在沒有後端時開發與驗證。
  ///
  /// 上台展示時若跑的是 demo 資料（`USE_BACKEND=false`），**不可以說那個勾是 AI 判斷的**
  /// ——要展示那件事就得接真後端。demo-plan.md 也把 Act 2 列為唯一不能造假的部分。
  @override
  Future<ChatReply> chat({
    required String elderId,
    required String lang,
    String? text,
    String? audioBase64,
  }) async {
    // 音檔那條 demo 轉錄不了——ASR 在後端，本機沒有任何東西聽得懂客語。畫面因此
    // 會顯示這句說明而不是長輩真正說的話，那是誠實的：不知道就說不知道，
    // 隨便編一句「今晡日食飽咧」會讓人以為辨識成功了。
    if (audioBase64 != null && audioBase64.isNotEmpty) {
      return ChatReply(
        conversationId: 'cnv_demo${DateTime.now().millisecondsSinceEpoch}',
        sessionId: _demoSessionId ??=
            'ses_demo${DateTime.now().millisecondsSinceEpoch}',
        transcript: '（示範資料：錄了 ${_approxKb(audioBase64)} KB 的音檔，但語音辨識在後端，這裡聽不懂）',
        replyText: '我有收到你的聲音，不過現在還沒接上聽得懂的後端。',
        replyAudioUrl: '',
        routinesUpdated: false,
      );
    }

    // 行程比對先做，而且不受回覆文字影響：RAG PoC 沒開的時候，這條路仍然要能走完
    // ——不然「講完話 → 今日畫面自己打勾」在沒有任何後端的預覽環境裡永遠測不到。
    final matched = await _matchCompletedRoutine(text ?? '');
    final replyText = await _demoReplyText(text ?? '', matched);
    return ChatReply(
      conversationId: 'cnv_demo${DateTime.now().millisecondsSinceEpoch}',
      sessionId: _demoSessionId ??=
          'ses_demo${DateTime.now().millisecondsSinceEpoch}',
      transcript: text ?? '',
      replyText: replyText,
      // demo 沒有 TTS 音檔；畫面在網址為空時退回裝置端 TTS。
      replyAudioUrl: '',
      routinesUpdated: matched != null,
    );
  }

  @override
  Future<void> closeChat() async => _demoSessionId = null;

  /// base64 還原成大概幾 KB（每 4 個字元 3 bytes）。只是給人看的量級，不求精確。
  static int _approxKb(String base64) => (base64.length * 3 ~/ 4) ~/ 1024;

  /// demo 的回覆文字：認出行程完成就回確認，否則問本機 RAG PoC，連不上才用罐頭句子。
  ///
  /// **比對到行程時不問 PoC**：PoC 是衛教知識庫問答，它不知道有行程這回事，對
  /// 「我早餐吃飽了」只會回「根據目前的資料庫，我找不到這個問題的答案」。那會湊出一個
  /// 自相矛盾的畫面——嘴上說找不到答案，今日行程卻已經打勾了。正式後端沒有這個問題
  /// （tool calling 的結果本來就會反映在回話裡），所以這裡讓 demo 這條路也自洽。
  ///
  /// 罐頭退路則是給「PoC 根本沒開」的情況：畫面的免手持迴圈靠「唸完回覆才接續聆聽」
  /// 串起來，回覆拿不到整條就斷掉。它只是為了讓路走得完，**不代表 AI 說了這句話**，
  /// 所以那句話自己會講明。
  Future<String> _demoReplyText(String text, RoutineOccurrence? matched) async {
    if (matched != null) return '好，「${matched.title}」我幫你記下來了。';
    try {
      final answer = await _api.ask(text);
      if (answer.answer.trim().isNotEmpty) return answer.answer;
    } catch (_) {
      // 落到下面的罐頭回覆
    }
    return '我聽到了。（示範資料：目前沒有連上對話後端，這句話不是 AI 產生的）';
  }

  /// 這句話有沒有講到某筆還沒完成的行程「做完了」。
  ///
  /// 粗糙但夠用的兩段判斷：句子裡要有完成語氣詞，而且要跟某筆行程的標題有至少兩個字
  /// 連續重疊（「血壓藥吃了」對上「吃血壓藥」的「血壓藥」）。兩個字是刻意的下限——
  /// 一個字會讓「吃飯了」誤中「吃血壓藥」。
  ///
  /// **取重疊最長的那一筆，而且比完才看完成狀態**，不是掃到第一個夠格的就收。
  /// demo 資料裡「吃血壓藥」與「量血壓」同時存在，先到先得會這樣錯：「吃血壓藥」
  /// 已完成 → 跳過 → 往下撞到「量血壓」也含「血壓」→ 打錯勾。而 demo 分鏡裡吃藥與
  /// 量血壓正好是分開的兩件事，這一錯台上就看得到。改成先選最像的那筆，若它已經完成
  /// 就什麼都不做——長輩再說一次「藥吃了」，答案本來就該是「已經記過了」。
  ///
  /// 這是 demo 用的鷹架，不是要模仿後端的判定邏輯（真的那套在 Bedrock Agent 那邊）。
  Future<RoutineOccurrence?> _matchCompletedRoutine(String text) async {
    const doneWords = ['了', '好', '完', '過'];
    if (!doneWords.any(text.contains)) return null;

    final items = await _mutableOccurrences(_occurrenceDate ?? _todayKey());
    RoutineOccurrence? best;
    var bestRun = 0;
    for (final o in items) {
      final run = _longestCommonRun(text, o.title);
      if (run < 2) continue; // 至少要兩個字才算數
      // 重疊一樣長時偏向還沒完成的那筆：「血壓量好了」對「吃血壓藥」與「量血壓」
      // 都只重疊「血壓」，而長輩顯然是在講還沒做的那件。
      final better = best == null ||
          run > bestRun ||
          (run == bestRun && best.status == 'done' && o.status != 'done');
      if (better) {
        bestRun = run;
        best = o;
      }
    }
    if (best == null || best.status == 'done') return null;
    // completed_by 是 conversation：這筆是對話裡認出來的，不是誰按的。
    return _markDone(best.routineId, by: 'conversation');
  }

  /// [a] 與 [b] 最長共同連續子字串的長度。
  static int _longestCommonRun(String a, String b) {
    var best = 0;
    for (var i = 0; i < b.length; i++) {
      for (var len = b.length - i; len > best; len--) {
        if (a.contains(b.substring(i, i + len))) {
          best = len;
          break;
        }
      }
    }
    return best;
  }

  /// demo 的 session id，[closeChat] 之後換一個新的。
  String? _demoSessionId;

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
  Future<Elder> createElder(Map<String, dynamic> fields) async {
    if (_elders == null) await elders();
    final list = _elders!;
    final created = Elder(
      // 真後端是 `"eld_" + uuid4().hex[:12]`；demo 用時間戳湊出同樣長度的十六進位。
      elderId:
          'eld_${DateTime.now().microsecondsSinceEpoch.toRadixString(16).padLeft(12, '0').substring(0, 12)}',
      name: fields['name'] as String? ?? '',
      nickname: fields['nickname'] as String?,
      birthYear: fields['birth_year'] as int?,
      gender: fields['gender'] as String?,
      langPreference: fields['lang_preference'] as String? ?? 'zh-TW',
      addressRegion: fields['address_region'] as String?,
      healthNotes: _healthNotes(fields['health_notes']),
      family: [
        for (final f in fields['family'] as List<dynamic>? ?? const [])
          if (f is Map<String, dynamic>) FamilyMember.fromJson(f),
      ],
      habitNote: fields['habit_note'] as String?,
      createdAt: DateTime.now(),
      updatedAt: DateTime.now(),
    );
    list.add(created);
    return Future.delayed(DemoData.latency, () => created);
  }

  /// 建立長輩時送進來的 `health_notes`。真後端接受純字串（相容舊契約）並一律
  /// 視為照護者填的，這裡跟著同一套規則。
  static List<HealthNote> _healthNotes(Object? raw) => [
        for (final v in raw as List<dynamic>? ?? const [])
          if (v is String && v.trim().isNotEmpty)
            HealthNote(
              noteId: _newNoteId(),
              text: v.trim(),
              createdAt: DateTime.now(),
            ),
      ];

  /// 真後端是 `"hn_" + uuid4().hex[:12]`；demo 用時間戳湊出同樣長度的十六進位。
  ///
  /// 同一微秒可能連續產生多筆（一次建立就送好幾項），補一個遞增值避免撞號——
  /// note_id 是刪除時的唯一依據，撞了就會刪錯那一筆。
  ///
  /// 取**尾端** 12 位：`microsecondsSinceEpoch` 的十六進位已經超過 12 位，
  /// 取前 12 位會把每次遞增的低位截掉，等於所有 ID 都一樣。
  static int _noteSeq = 0;
  static String _newNoteId() {
    final seed = DateTime.now().microsecondsSinceEpoch + _noteSeq++;
    final hex = seed.toRadixString(16).padLeft(12, '0');
    return 'hn_${hex.substring(hex.length - 12)}';
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

  @override
  Future<Elder> addHealthNote({
    required String elderId,
    required String text,
  }) async {
    final updated = await _mutateHealthNotes(
      elderId,
      (notes) => [
        ...notes,
        HealthNote(
          noteId: _newNoteId(),
          text: text.trim(),
          createdAt: DateTime.now(),
        ),
      ],
    );
    return Future.delayed(DemoData.latency, () => updated);
  }

  @override
  Future<Elder> removeHealthNote({
    required String elderId,
    required String noteId,
  }) async {
    final updated = await _mutateHealthNotes(
      elderId,
      (notes) => [
        for (final n in notes)
          if (n.noteId != noteId) n,
      ],
    );
    return Future.delayed(DemoData.latency, () => updated);
  }

  Future<Elder> _mutateHealthNotes(
    String elderId,
    List<HealthNote> Function(List<HealthNote>) change,
  ) async {
    if (_elders == null) await elders();
    final list = _elders!;
    final i = list.indexWhere((e) => e.elderId == elderId);
    if (i < 0) throw StateError('demo 資料裡沒有這位長者：$elderId');

    final updated = list[i].copyWith(
      healthNotes: change(list[i].healthNotes),
      updatedAt: DateTime.now(),
    );
    list[i] = updated;
    return updated;
  }

  // ---- 綁定照護者 ----

  /// demo 沒有帳號系統可查，改用**格式**判斷 ID 是否存在：`cg_` 後接 8 個十六進位
  /// 字元（api.md 的格式）。不合格式就當查無此人。
  ///
  /// 這條規則抓得到真正常見的錯誤——少打一碼、把 `cg_` 漏掉、把 0 打成 O——
  /// 而那正是長輩抄 ID 時會發生的事。格式對但不存在的 ID 在 demo 裡驗不出來，
  /// 那要等真的帳號系統。
  static final _caregiverIdPattern = RegExp(r'^cg_[0-9a-f]{8}$');

  @override
  Future<CaregiverLink> linkCaregiver({
    required String elderId,
    required String caregiverId,
  }) async {
    // 大小寫不敏感、前後空白忽略（api.md）。
    final id = caregiverId.trim().toLowerCase();
    if (!_caregiverIdPattern.hasMatch(id)) {
      throw const ApiException('找不到這個 ID',
          statusCode: 404, code: ApiErrorCodes.caregiverNotFound);
    }

    final list = await caregivers(elderId: elderId);
    final existing = list.where((c) => c.caregiverId == id).firstOrNull;
    if (existing != null) {
      // 已綁定：linked_at 不刷新（api.md），所以原樣回傳。
      return (caregiver: existing, isNew: false);
    }

    final linked = Caregiver(
      caregiverId: id,
      // 真後端從 Cognito 取顯示名稱；demo 沒有帳號可查，用 ID 尾碼湊一個看得出是誰的字樣。
      name: '家人 ${id.substring(id.length - 4)}',
      linkedAt: DateTime.now(),
    );
    await _saveCaregivers(elderId, [...list, linked]);
    return Future.delayed(
        DemoData.latency, () => (caregiver: linked, isNew: true));
  }

  @override
  Future<List<Caregiver>> caregivers({required String elderId}) async {
    final cached = _caregiversByElder[elderId];
    if (cached != null) return List.of(cached);

    final p = await SharedPreferences.getInstance();
    final raw = p.getStringList(_caregiverStoreKey(elderId)) ?? const [];
    final list = <Caregiver>[];
    for (final entry in raw) {
      // 寫壞或換過格式的資料直接跳過，不要讓整份清單載不出來。
      try {
        final decoded = jsonDecode(entry);
        if (decoded is Map<String, dynamic>) {
          list.add(Caregiver.fromJson(decoded));
        }
      } catch (_) {
        continue;
      }
    }
    _caregiversByElder[elderId] = list;
    return List.of(list);
  }

  /// 已綁定的家人，依長者分開存。
  ///
  /// **這是 demo 狀態裡唯一有持久化的一份**，其餘（行程、長者、當日 occurrence）
  /// 都只活在記憶體。理由是連結家人在 demo 流程裡是**一次性的設定**——照護者在
  /// Act 1 綁一次，後面每一幕都預設它還在；每次重開 App 都要重綁的話，等於每次
  /// 排練都得多做一段跟當幕無關的操作，也很容易在台上忘記。
  ///
  /// 以 `elder_id` 為 key：綁定本來就是 per-elder（api.md 的端點就長這樣），
  /// 換長輩自然分開，不會看到別人的家人。
  final Map<String, List<Caregiver>> _caregiversByElder = {};

  static String _caregiverStoreKey(String elderId) =>
      'demo_linked_caregivers_$elderId';

  Future<void> _saveCaregivers(String elderId, List<Caregiver> list) async {
    _caregiversByElder[elderId] = list;
    final p = await SharedPreferences.getInstance();
    await p.setStringList(
      _caregiverStoreKey(elderId),
      // 欄位名沿用 api.md，存進去的東西才能原樣走 Caregiver.fromJson 回來。
      [
        for (final c in list)
          jsonEncode({
            'caregiver_id': c.caregiverId,
            'name': c.name,
            if (c.linkedAt != null) 'linked_at': c.linkedAt!.toIso8601String(),
          }),
      ],
    );
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
  Future<void> deleteRoutine(String routineId,
      {required String clientRequestId}) async {
    // demo 直接從清單移除。後端那條目前是 active=false（資料還在），兩邊對畫面
    // 而言一樣：都看不到了。等後端定案再對齊。
    final list = await _mutableRoutines(null);
    list.removeWhere((r) => r.routineId == routineId);
    return Future.delayed(DemoData.latency, () {});
  }

  @override
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  }) async {
    final items = await _mutableOccurrences(date);
    return DailyRoutineView(date: date, items: List.of(items));
  }

  @override
  Future<RoutineOccurrence> completeRoutine(
    RoutineOccurrence occurrence, {
    String? date,
  }) async {
    // completed_by 是 elder：這條路是長者自己在今日畫面按下確認的。
    // 對話裡由 AI 判定完成的那條走 [chat]，記的是 conversation。
    final done = await _markDone(occurrence.routineId, by: 'elder');
    return done ?? await DemoData.completeRoutine(occurrence);
  }

  /// 取可寫的行程清單（必要時先灌初始資料）。
  Future<List<Routine>> _mutableRoutines(String? elderId) async {
    if (_routines == null) {
      await routines(elderId: elderId ?? DemoData.elderId);
    }
    return _routines!;
  }

  /// 取可寫的當日 occurrence（必要時先灌初始資料）。
  ///
  /// 跨日換一份：demo 開著過午夜的機會不高，但拿昨天的清單當今天用會直接讓
  /// 今日畫面顯示錯的日期資料，成本遠高於多存一個字串。
  Future<List<RoutineOccurrence>> _mutableOccurrences(String date) async {
    if (_occurrences == null || _occurrenceDate != date) {
      final view = await DemoData.dailyRoutines(date);
      _occurrences = List.of(view.items);
      _occurrenceDate = date;
    }
    return _occurrences!;
  }

  /// 把某筆 occurrence 標成完成；找不到（或已完成）回 null。
  Future<RoutineOccurrence?> _markDone(String routineId,
      {required String by}) async {
    final date = _occurrenceDate ?? _todayKey();
    final items = await _mutableOccurrences(date);
    final i = items.indexWhere((o) => o.routineId == routineId);
    if (i < 0) return null;

    final done = RoutineOccurrence(
      routineId: items[i].routineId,
      title: items[i].title,
      type: items[i].type,
      scheduledAt: items[i].scheduledAt,
      status: 'done',
      completedAt: DateTime.now(),
      completedBy: by,
    );
    items[i] = done;
    return done;
  }

  static String _todayKey() {
    final n = DateTime.now();
    return '${n.year}-${_two(n.month)}-${_two(n.day)}';
  }

  static String _two(int v) => v.toString().padLeft(2, '0');

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
