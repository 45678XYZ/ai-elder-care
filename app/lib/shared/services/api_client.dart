import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/api_config.dart';
import '../models/ask_result.dart';
import '../models/caregiver.dart';
import '../models/chat_reply.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/api_page.dart';
import '../models/routine.dart';
import '../models/session_close.dart';
import '../models/stats.dart';
import 'api_exception.dart';

/// 後端 API 客戶端。端點、欄位、錯誤格式與分頁規則一律以 docs/api.md 為準。
///
/// 本類自己的實作決定（api.md 未規範的部分）：
/// - 認證 token 由 [ApiClient.new] 的 `tokenProvider` 提供（登入接上 Cognito 後注入），
///   尚未接上時不帶 Authorization header。
/// - 本類是**薄的一層**：只負責組請求、解回應、把錯誤轉成 [ApiException]，
///   不做重試、不持有 session 狀態。冪等鍵（`client_request_id`）一律由呼叫端提供並持有，
///   因為重送必須沿用同一個值——長者對話的那一份由 [ChatSession] 管。
///
/// [ask] 是過渡端點——打本機 RAG PoC 的 `/ask`（無認證、無 `/v1` 前綴），先驗證問答串接；
/// 正式後端上線後由 [chat] 取代。
class ApiClient {
  ApiClient({
    String? baseUrl,
    http.Client? httpClient,
    Future<String?> Function()? tokenProvider,
  })  : _baseUrl = baseUrl ?? ApiConfig.baseUrl,
        _http = httpClient ?? http.Client(),
        _tokenProvider = tokenProvider;

  final String _baseUrl;
  final http.Client _http;

  /// 取得目前 Cognito ID Token；回傳 null 表示尚未登入。
  final Future<String?> Function()? _tokenProvider;

  // ---- RAG PoC（過渡）----

  /// 送一句問題到本機 RAG PoC 的 `POST /ask`，回傳答案與引用來源。
  ///
  /// 過渡用端點：契約（`{question}` → `{answer, sources}`）比正式 [chat] 簡單，沒有語音、
  /// 對話 ID、行程更新旗標。正式後端上線後由 [chat] 取代。連線或非 200 時丟 [ApiException]。
  Future<AskResult> ask(String question) async {
    final http.Response res;
    try {
      res = await _http.post(
        Uri.parse('$_baseUrl/ask'),
        headers: const {'Content-Type': 'application/json; charset=utf-8'},
        body: jsonEncode({'question': question}),
      );
    } catch (e) {
      throw ApiException('無法連線到後端：$e');
    }

    if (res.statusCode != 200) {
      throw ApiException(_errorMessage(res), statusCode: res.statusCode);
    }
    // http 套件的 body getter 依 content-type charset 以 UTF-8 解碼。
    return AskResult.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  // ---- 對話（長者模式）----

  /// `POST /chat` — realtime 對話快路徑。[text] 與 [audioBase64] 擇一（audio 為 base64）。
  ///
  /// [clientRequestId] 是冪等鍵：**每次新的長者輸入產生新值，同一次輸入重送沿用原值**。
  /// 呼叫端不要在這裡臨時生成——重試時值必須一樣，否則會重複建立 routine／event。
  /// 一般直接用 [ChatSession]，它已代管冪等鍵與 session。
  ///
  /// [sessionId] 第一輪省略；之後帶回前一輪回應的值。後端可能因原 session 已 idle／關閉／
  /// 達上限而改用新 session，實際使用的以回應的 [ChatReply.sessionId] 為準。
  ///
  /// text 與 audio 同時給或同時空白時丟 [ArgumentError]——這是呼叫端的錯，
  /// 不必送出去等後端回 400。
  Future<ChatReply> chat({
    required String clientRequestId,
    required String elderId,
    required String lang,
    String? sessionId,
    String? text,
    String? audioBase64,
    String audioFormat = 'm4a',
  }) async {
    final hasText = text != null && text.isNotEmpty;
    final hasAudio = audioBase64 != null && audioBase64.isNotEmpty;
    if (hasText == hasAudio) {
      throw ArgumentError('text 與 audio 擇一必填，不可同時給或同時空白。');
    }

    final json = await _request('POST', '/chat', body: {
      'client_request_id': clientRequestId,
      if (sessionId != null) 'session_id': sessionId,
      'elder_id': elderId,
      'lang': lang,
      if (text != null) 'text': text,
      if (audioBase64 != null)
        'audio': {'data': audioBase64, 'format': audioFormat},
    });
    return ChatReply.fromJson(json);
  }

  /// `POST /chat/sessions/{id}/close` — 明確關閉 session。
  ///
  /// 停止免手持互動、離開對話畫面或切換長者前呼叫；會凍結快照並啟動離線事件整理。
  /// 只有 token 對應的長者本人可呼叫。
  ///
  /// 冪等靠 session 狀態，**不需要也不可帶 `client_request_id`**：還有 turn 在處理時
  /// 回 409 `REQUEST_IN_PROGRESS`，退避後重送同一個呼叫即可（[ChatSession.close] 已代辦）。
  Future<SessionCloseResult> closeSession(String sessionId) async {
    // body 可省略，但送出時 api.md 要求必須是空 object。
    final json = await _request(
      'POST',
      '/chat/sessions/$sessionId/close',
      body: const <String, dynamic>{},
    );
    return SessionCloseResult.fromJson(json);
  }

  // ---- 呼叫者身分 ----

  /// `GET /me` — 呼叫者自己的身分（照護者 ID 與顯示名稱）。
  ///
  /// 照護者要把 ID 報給家人（長輩手機上的「連結家人」輸入它），得先看得到自己的 ID。
  /// 長者帳號呼叫回 403 `FORBIDDEN`，所以只在照護者模式呼叫。
  Future<Caregiver> getMe() async {
    final json = await _request('GET', '/me');
    return Caregiver.fromJson(json);
  }

  // ---- 長者資料 ----

  /// `GET /elders` — 長者列表（一頁）。
  ///
  /// 續頁把上一頁的 [ApiPage.nextToken] 原樣帶進 [nextToken]；[getAllElders] 是自動翻完的版本。
  Future<ApiPage<Elder>> getElders({int? limit, String? nextToken}) async {
    final json = await _request('GET', '/elders',
        query: _pageQuery(limit: limit, nextToken: nextToken));
    return ApiPage.fromJson(json, Elder.fromJson);
  }

  /// 翻完所有頁的長者列表。照護者綁定的長者數量有限，一次載齊比讓 UI 處理分頁單純。
  Future<List<Elder>> getAllElders({int? limit}) =>
      _drain((token) => getElders(limit: limit, nextToken: token));

  /// `GET /elders/{id}` — 單筆長者。
  Future<Elder> getElder(String elderId) async {
    final json = await _request('GET', '/elders/$elderId');
    return Elder.fromJson(json);
  }

  /// `POST /elders` — 建立長者。[fields] 的必填欄位見 docs/api.md。
  Future<Elder> createElder(Map<String, dynamic> fields) async {
    final json = await _request('POST', '/elders', body: fields);
    return Elder.fromJson(json);
  }

  /// `PATCH /elders/{id}` — 部分更新長者。
  Future<Elder> updateElder(String elderId, Map<String, dynamic> fields) async {
    final json = await _request('PATCH', '/elders/$elderId', body: fields);
    return Elder.fromJson(json);
  }

  /// `POST /elders/{id}/health_notes` — 新增一筆健康註記。
  ///
  /// 與 [updateElder] 送整份 `health_notes` 的差別在於這裡是原子 append：這個欄位
  /// 對話中的 AI 也會寫（api.md），整份覆寫會把對方期間寫進去的內容一起蓋掉。
  ///
  /// 不帶 `source`——來源由後端依寫入路徑決定，client 指定會被回 400。
  Future<Elder> addHealthNote(String elderId, String text) async {
    final json = await _request('POST', '/elders/$elderId/health_notes',
        body: {'text': text});
    return Elder.fromJson(json);
  }

  /// `DELETE /elders/{id}/health_notes/{note_id}` — 刪除一筆健康註記。
  ///
  /// 找不到那一筆回 404 `HEALTH_NOTE_NOT_FOUND`。
  Future<Elder> removeHealthNote(String elderId, String noteId) async {
    final json =
        await _request('DELETE', '/elders/$elderId/health_notes/$noteId');
    return Elder.fromJson(json);
  }

  // ---- 綁定照護者 ----

  /// `POST /elders/{id}/caregivers` — 把一位照護者綁到這位長者（長者本人呼叫）。
  ///
  /// 回傳的 [CaregiverLink.isNew] 區分兩種成功：201 是這次才綁上的、200 是早就綁過了。
  /// 兩者都算成功，但畫面要說不同的話——長輩才知道剛才那一下到底有沒有作用。
  ///
  /// 靠「長者＋照護者」天然冪等，不需要 `client_request_id`：重送走同一條路。
  /// 查無此 ID 回 404 `CAREGIVER_NOT_FOUND`（打錯字就是這個）；`elder_id` 不存在或
  /// 不屬於本人一律回 404 `ELDER_NOT_FOUND`，不以 403 區分，避免洩漏某位長者是否存在。
  Future<CaregiverLink> linkCaregiver({
    required String elderId,
    required String caregiverId,
  }) async {
    final (json, status) = await _requestWithStatus(
      'POST',
      '/elders/$elderId/caregivers',
      body: {'caregiver_id': caregiverId},
    );
    return (caregiver: Caregiver.fromJson(json), isNew: status == 201);
  }

  /// `GET /elders/{id}/caregivers` — 已綁定的家人（已翻完所有頁）。
  ///
  /// 按 `linked_at` 由舊到新。長者本人與已綁定的照護者都讀得到。
  Future<List<Caregiver>> getCaregivers(String elderId) =>
      _drain((token) async {
        final json = await _request('GET', '/elders/$elderId/caregivers',
            query: _pageQuery(nextToken: token));
        return ApiPage.fromJson(json, Caregiver.fromJson);
      });

  // ---- 每日摘要（照護者）----

  /// `GET /summaries` — 每日摘要列表（一頁）。[from]/[to] 為日期，含首尾，預設最近 7 天。
  ///
  /// 回傳的摘要可能是 partial（見 [DailySummary.isPartial]）——當日仍有對話未整理完，
  /// UI 要據此提示，不可當成當日全貌。
  Future<ApiPage<DailySummary>> getSummaries({
    required String elderId,
    String? from,
    String? to,
    int? limit,
    String? nextToken,
  }) async {
    final json = await _request('GET', '/summaries', query: {
      'elder_id': elderId,
      if (from != null) 'from': from,
      if (to != null) 'to': to,
      ..._pageQuery(limit: limit, nextToken: nextToken),
    });
    return ApiPage.fromJson(json, DailySummary.fromJson);
  }

  /// `POST /summaries/generate` — 手動觸發生成（Demo 用）。
  Future<DailySummary> generateSummary({
    required String elderId,
    String? date,
  }) async {
    final json = await _request('POST', '/summaries/generate', body: {
      'elder_id': elderId,
      if (date != null) 'date': date,
    });
    return DailySummary.fromJson(json);
  }

  // ---- 生活事件（時間軸，照護者）----

  /// `GET /events` — 生活事件（一頁）。[from]/[to] 為日期（預設今天），[type] 選填過濾。
  ///
  /// 按 `ts` 最新優先、跨頁順序穩定；時間軸捲到底時把 [ApiPage.nextToken] 帶進 [nextToken] 續拉。
  ///
  /// 注意可見時機（api.md）：例行公事完成與高風險事件在 `/chat` 回應前就查得到，
  /// **一般生活事件要等 session 關閉且批次整理完成**才會出現。
  Future<ApiPage<LifeEvent>> getEvents({
    required String elderId,
    String? from,
    String? to,
    String? type,
    int? limit,
    String? nextToken,
  }) async {
    final json = await _request('GET', '/events', query: {
      'elder_id': elderId,
      if (from != null) 'from': from,
      if (to != null) 'to': to,
      if (type != null) 'type': type,
      ..._pageQuery(limit: limit, nextToken: nextToken),
    });
    return ApiPage.fromJson(json, LifeEvent.fromJson);
  }

  // ---- 例行公事 ----

  /// `GET /routines?elder_id=` — 例行公事定義列表（App 據此排本地通知）。
  ///
  /// 排通知要看到全部定義，所以這裡自動翻完所有頁再回傳。
  /// `/chat` 回 `routines_updated=true` 時應背景重拉此端點並重排通知。
  Future<List<Routine>> getRoutines({required String elderId}) =>
      _drain((token) async {
        final json = await _request('GET', '/routines', query: {
          'elder_id': elderId,
          ..._pageQuery(nextToken: token),
        });
        return ApiPage.fromJson(json, Routine.fromJson);
      });

  /// `GET /routines?elder_id=&date=` — 當日行程視圖（展開該日 occurrence 與完成狀態）。
  Future<DailyRoutineView> getDailyRoutines({
    required String elderId,
    required String date,
  }) async {
    final json = await _request('GET', '/routines', query: {
      'elder_id': elderId,
      'date': date,
    });
    return DailyRoutineView.fromJson(json);
  }

  /// `POST /routines` — 建立例行公事（照護者）。
  ///
  /// [clientRequestId] 必填：後端用它算出 `routine_id`，所以同一個值重送拿到同一筆，
  /// 不會建出兩筆重複行程。送出前先產生並持有，重送沿用；換內容要換新值，
  /// 否則回 409 `IDEMPOTENCY_CONFLICT`。
  ///
  /// 對話中建立的 routine 由後端直接寫入，不經此端點。
  Future<Routine> createRoutine({
    required String clientRequestId,
    required Map<String, dynamic> fields,
  }) async {
    final json = await _request('POST', '/routines', body: {
      'client_request_id': clientRequestId,
      ...fields,
    });
    return Routine.fromJson(json);
  }

  /// `PATCH /routines/{id}` — 修改／停用例行公事（照護者）。
  ///
  /// [clientRequestId] 必填且**每次修改都要新的一個**（同一個值代表同一次修改，
  /// 重送不會建出第二個版本）。[fields] 只可含 `title`、`type`、`schedule`、`remind`、
  /// `active`——其他欄位後端回 400 `INVALID_PARAMETER`。
  Future<Routine> updateRoutine(
    String routineId, {
    required String clientRequestId,
    required Map<String, dynamic> fields,
  }) async {
    final json = await _request('PATCH', '/routines/$routineId', body: {
      'client_request_id': clientRequestId,
      ...fields,
    });
    return Routine.fromJson(json);
  }

  /// `POST /routines/{id}/complete` — 手動確認完成（兩端）。
  ///
  /// 靠「長者＋routine＋日期」天然冪等，不需要 `client_request_id`：已完成的重送回同一筆，
  /// 不會重複記事件。指定日期無排程時回 400 `ROUTINE_NOT_SCHEDULED`。
  Future<RoutineOccurrence> completeRoutine(
    String routineId, {
    String? date,
  }) async {
    final json = await _request('POST', '/routines/$routineId/complete', body: {
      if (date != null) 'date': date,
    });
    return RoutineOccurrence.fromJson(json);
  }

  // ---- 統計（照護者）----

  /// `GET /stats` — 統計（今日/期間互動、例行公事完成、逐日趨勢）。
  Future<Stats> getStats({required String elderId, int days = 7}) async {
    final json = await _request('GET', '/stats', query: {
      'elder_id': elderId,
      'days': '$days',
    });
    return Stats.fromJson(json);
  }

  // ---- 內部：HTTP 與錯誤處理 ----

  /// 統一送出 `/v1` 底下的請求，處理認證 header、連線錯誤與非 2xx 狀態碼。
  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, String>? query,
    Object? body,
  }) async =>
      (await _requestWithStatus(method, path, query: query, body: body)).$1;

  /// 同 [_request]，但連狀態碼一起給。
  ///
  /// 只有綁定照護者用得到：那個端點用 201／200 區分「這次才綁上」與「早就綁過了」，
  /// body 兩者相同，不看狀態碼就分不出來（api.md）。其餘端點看 body 就夠，走 [_request]。
  Future<(Map<String, dynamic>, int)> _requestWithStatus(
    String method,
    String path, {
    Map<String, String>? query,
    Object? body,
  }) async {
    var uri = Uri.parse('$_baseUrl/v1$path');
    if (query != null && query.isNotEmpty) {
      uri = uri.replace(queryParameters: query);
    }
    final headers = await _headers();
    final payload = body == null ? null : jsonEncode(body);

    late final http.Response res;
    try {
      switch (method) {
        case 'GET':
          res = await _http.get(uri, headers: headers);
          break;
        case 'POST':
          res = await _http.post(uri, headers: headers, body: payload);
          break;
        case 'PATCH':
          res = await _http.patch(uri, headers: headers, body: payload);
          break;
        case 'DELETE':
          res = await _http.delete(uri, headers: headers, body: payload);
          break;
        default:
          throw ArgumentError('未支援的 method：$method');
      }
    } catch (e) {
      if (e is ApiException) rethrow;
      throw ApiException('無法連線到後端：$e');
    }

    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw ApiException(
        _errorMessage(res),
        statusCode: res.statusCode,
        code: _errorCode(res),
      );
    }
    if (res.body.isEmpty) return (const <String, dynamic>{}, res.statusCode);
    return (jsonDecode(res.body) as Map<String, dynamic>, res.statusCode);
  }

  /// 組請求 header；有 token 才帶 Authorization。
  Future<Map<String, String>> _headers() async {
    final token = await _tokenProvider?.call();
    return {
      'Content-Type': 'application/json; charset=utf-8',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  /// 組分頁的 query 參數。`next_token` 是不透明游標，原樣送回，不解析也不重組。
  Map<String, String> _pageQuery({int? limit, String? nextToken}) => {
        if (limit != null) 'limit': '$limit',
        if (nextToken != null && nextToken.isNotEmpty) 'next_token': nextToken,
      };

  /// 反覆呼叫 [fetchPage] 直到沒有 `next_token`，把各頁 items 串成一個 list。
  ///
  /// 只用於「總量本來就有限、UI 需要完整資料」的列表（如長者清單、排通知用的行程定義）；
  /// 事件時間軸這種會長大的資料請讓 UI 自己一頁一頁拉。
  Future<List<T>> _drain<T>(
    Future<ApiPage<T>> Function(String? nextToken) fetchPage,
  ) async {
    final all = <T>[];
    String? token;
    do {
      final page = await fetchPage(token);
      all.addAll(page.items);
      token = page.nextToken;
    } while (token != null);
    return all;
  }

  /// 從錯誤回應取 body 的 `error.code`。
  String? _errorCode(http.Response res) {
    try {
      final body = jsonDecode(res.body);
      if (body is Map && body['error'] is Map) {
        return (body['error'] as Map)['code']?.toString();
      }
    } catch (_) {
      // 非 JSON body
    }
    return null;
  }

  /// 從錯誤回應盡量取出可讀訊息：優先正式後端的 `{error:{message}}`，
  /// 否則退回 FastAPI PoC 的 `{detail}`，再退回泛用訊息。
  String _errorMessage(http.Response res) {
    try {
      final body = jsonDecode(res.body);
      if (body is Map<String, dynamic>) {
        final err = body['error'];
        if (err is Map && err['message'] != null) {
          return err['message'].toString();
        }
        if (body['detail'] != null) return body['detail'].toString();
      }
    } catch (_) {
      // 非 JSON body，往下用泛用訊息
    }
    return '請求失敗（HTTP ${res.statusCode}）';
  }

  /// 釋放底層 HTTP 連線。使用端（如畫面 dispose 時）呼叫。
  void dispose() => _http.close();
}
