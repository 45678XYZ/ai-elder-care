import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/api_config.dart';
import '../models/ask_result.dart';
import '../models/chat_reply.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/page.dart';
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

  // ---- 長者資料 ----

  /// `GET /elders` — 長者列表（一頁）。
  ///
  /// 續頁把上一頁的 [Page.nextToken] 原樣帶進 [nextToken]；[getAllElders] 是自動翻完的版本。
  Future<Page<Elder>> getElders({int? limit, String? nextToken}) async {
    final json = await _request('GET', '/elders',
        query: _pageQuery(limit: limit, nextToken: nextToken));
    return Page.fromJson(json, Elder.fromJson);
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

  // ---- 每日摘要（照護者）----

  /// `GET /summaries` — 每日摘要列表（一頁）。[from]/[to] 為日期，含首尾，預設最近 7 天。
  ///
  /// 回傳的摘要可能是 partial（見 [DailySummary.isPartial]）——當日仍有對話未整理完，
  /// UI 要據此提示，不可當成當日全貌。
  Future<Page<DailySummary>> getSummaries({
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
    return Page.fromJson(json, DailySummary.fromJson);
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
  /// 按 `ts` 最新優先、跨頁順序穩定；時間軸捲到底時把 [Page.nextToken] 帶進 [nextToken] 續拉。
  ///
  /// 注意可見時機（api.md）：例行公事完成與高風險事件在 `/chat` 回應前就查得到，
  /// **一般生活事件要等 session 關閉且批次整理完成**才會出現。
  Future<Page<LifeEvent>> getEvents({
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
    return Page.fromJson(json, LifeEvent.fromJson);
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
        return Page.fromJson(json, Routine.fromJson);
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
    if (res.body.isEmpty) return const <String, dynamic>{};
    return jsonDecode(res.body) as Map<String, dynamic>;
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
    Future<Page<T>> Function(String? nextToken) fetchPage,
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
