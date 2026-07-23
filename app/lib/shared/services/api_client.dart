import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/api_config.dart';
import '../models/ask_result.dart';
import '../models/chat_reply.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/routine.dart';
import '../models/stats.dart';
import 'api_exception.dart';

/// 後端 API 客戶端。端點、欄位、錯誤格式與分頁規則一律以 docs/api.md 為準。
///
/// 本類自己的實作決定（api.md 未規範的部分）：
/// - 認證 token 由 [ApiClient.new] 的 `tokenProvider` 提供（登入接上 Cognito 後注入），
///   尚未接上時不帶 Authorization header。
/// - 列表端點目前只取第一頁，分頁尚未實作（見各方法 TODO）。
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

  /// `POST /chat` — 對話核心。[text] 與 [audioBase64] 擇一（audio 為 base64）。
  ///
  /// 兩者同時給或同時空白時丟 [ArgumentError]——這是呼叫端的錯，不必送出去等後端回 400。
  Future<ChatReply> chat({
    required String elderId,
    required String lang,
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
      'elder_id': elderId,
      'lang': lang,
      if (text != null) 'text': text,
      if (audioBase64 != null)
        'audio': {'data': audioBase64, 'format': audioFormat},
    });
    return ChatReply.fromJson(json);
  }

  // ---- 長者資料 ----

  /// `GET /elders` — 長者列表。
  Future<List<Elder>> getElders() async {
    final json = await _request('GET', '/elders');
    return _items(json).map(Elder.fromJson).toList();
  }

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

  /// `GET /summaries` — 每日摘要列表。[from]/[to] 為日期。
  Future<List<DailySummary>> getSummaries({
    required String elderId,
    String? from,
    String? to,
  }) async {
    final json = await _request('GET', '/summaries', query: {
      'elder_id': elderId,
      if (from != null) 'from': from,
      if (to != null) 'to': to,
    });
    return _items(json).map(DailySummary.fromJson).toList();
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

  /// `GET /events` — 生活事件。[from]/[to] 為日期，[type] 選填過濾。
  // TODO: 分頁（next_token）——目前只取第一頁。
  Future<List<LifeEvent>> getEvents({
    required String elderId,
    String? from,
    String? to,
    String? type,
  }) async {
    final json = await _request('GET', '/events', query: {
      'elder_id': elderId,
      if (from != null) 'from': from,
      if (to != null) 'to': to,
      if (type != null) 'type': type,
    });
    return _items(json).map(LifeEvent.fromJson).toList();
  }

  // ---- 例行公事 ----

  /// `GET /routines?elder_id=` — 例行公事定義列表（App 據此排本地通知）。
  Future<List<Routine>> getRoutines({required String elderId}) async {
    final json = await _request('GET', '/routines', query: {
      'elder_id': elderId,
    });
    return _items(json).map(Routine.fromJson).toList();
  }

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

  /// `POST /routines` — 建立例行公事。
  Future<Routine> createRoutine(Map<String, dynamic> fields) async {
    final json = await _request('POST', '/routines', body: fields);
    return Routine.fromJson(json);
  }

  /// `PATCH /routines/{id}` — 修改／停用例行公事。
  Future<Routine> updateRoutine(
    String routineId,
    Map<String, dynamic> fields,
  ) async {
    final json = await _request('PATCH', '/routines/$routineId', body: fields);
    return Routine.fromJson(json);
  }

  /// `POST /routines/{id}/complete` — 手動確認完成。
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

  /// 取列表回應的 `items` 陣列。
  List<Map<String, dynamic>> _items(Map<String, dynamic> json) =>
      (json['items'] as List<dynamic>? ?? const [])
          .cast<Map<String, dynamic>>();

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
