/// 後端 API 客戶端。規格見 docs/api.md。
///
/// 共通慣例：
/// - 所有請求帶 Authorization: Bearer <Cognito ID Token>
/// - 錯誤 body 為 { "error": { "code", "message" } }（401 除外，由 API Gateway 回應）
/// - 列表分頁：?limit= 與 ?next_token=
library;

import 'dart:convert';
import 'dart:math';

import 'package:http/http.dart' as http;

/// 辨識語言。wire 值必須與 api.md 的 `Language` enum 一致。
enum ChatLanguage {
  zhTw('zh-TW'),
  hak('hak');

  const ChatLanguage(this.wireValue);

  final String wireValue;

  /// 裝置 TTS fallback 要求的 locale；只做 capability 判斷，不代表已安裝 voice。
  String get deviceTtsLocale => this == ChatLanguage.zhTw ? 'zh-TW' : 'hak-TW';

  /// 只有裝置回報精確支援要求 locale 時才可嘗試 OS TTS，否則保留文字／重試。
  bool canUseDeviceTtsFallback(Iterable<String> supportedLocales) {
    final requiredLocale = deviceTtsLocale.toLowerCase();
    return supportedLocales.any(
      (locale) => locale.replaceAll('_', '-').toLowerCase() == requiredLocale,
    );
  }
}

/// 客語 ASR 與 TTS 共用腔調；來源只允許 elder profile。
enum HakkaDialect {
  sixian('htia_sixian'),
  hailu('htia_hailu'),
  dapu('htia_dapu'),
  raoping('htia_raoping'),
  zhaoan('htia_zhaoan'),
  nansixian('htia_nansixian');

  const HakkaDialect(this.wireValue);

  final String wireValue;

  static HakkaDialect fromWireValue(String value) => values.firstWhere(
        (dialect) => dialect.wireValue == value,
        orElse: () => HakkaDialect.sixian,
      );
}

/// Elder profile 中供 ASR/TTS 共用的語音偏好投影。
class ElderVoicePreferences {
  const ElderVoicePreferences({
    required this.language,
    required this.hakkaDialect,
  });

  factory ElderVoicePreferences.fromJson(Map<String, Object?> json) {
    final languageValue = json['lang_preference'] as String? ?? 'zh-TW';
    return ElderVoicePreferences(
      language: ChatLanguage.values.firstWhere(
        (language) => language.wireValue == languageValue,
        orElse: () => ChatLanguage.zhTw,
      ),
      hakkaDialect: HakkaDialect.fromWireValue(
        json['hakka_dialect'] as String? ?? HakkaDialect.sixian.wireValue,
      ),
    );
  }

  final ChatLanguage language;
  final HakkaDialect hakkaDialect;

  Map<String, Object?> toJson() => {
        'lang_preference': language.wireValue,
        'hakka_dialect': hakkaDialect.wireValue,
      };
}

/// 上傳音訊的容器格式。後端只接受這兩種。
enum AudioFormat {
  wav('wav'),
  m4a('m4a');

  const AudioFormat(this.wireValue);

  final String wireValue;
}

/// 送往 `POST /chat` 的音訊輸入。單句 ≤ 60 秒，否則後端回 400 `AUDIO_TOO_LONG`。
class AudioInput {
  const AudioInput({required this.base64Data, required this.format});

  /// base64 編碼後的音訊位元組。
  final String base64Data;
  final AudioFormat format;

  Map<String, Object?> toJson() => {
        'data': base64Data,
        'format': format.wireValue,
      };
}

/// `POST /chat` 的成功回應。
class ChatResult {
  const ChatResult({
    required this.conversationId,
    required this.sessionId,
    required this.transcript,
    required this.replyText,
    required this.replyAudioUrl,
    required this.routinesUpdated,
  });

  factory ChatResult.fromJson(Map<String, Object?> json) {
    return ChatResult(
      conversationId: json['conversation_id'] as String? ?? '',
      sessionId: json['session_id'] as String? ?? '',
      transcript: json['transcript'] as String? ?? '',
      replyText: json['reply_text'] as String? ?? '',
      replyAudioUrl: json['reply_audio_url'] as String?,
      routinesUpdated: json['routines_updated'] as bool? ?? false,
    );
  }

  /// 本 turn 的 ID；相同 client_request_id 重送會得到同一個值。
  final String conversationId;

  /// 本 turn 實際使用的 session；下一輪必須帶回這個值。
  final String sessionId;
  final String transcript;
  final String replyText;

  /// 15 分鐘有效的 S3 presigned URL；TTS 全部失敗時為 null，文字回覆仍成立。
  final String? replyAudioUrl;

  bool get hasReplyAudio => replyAudioUrl?.isNotEmpty ?? false;

  /// 為 true 時應背景重拉 `GET /routines` 並重排本地通知。
  final bool routinesUpdated;
}

/// 後端回傳的具型別錯誤。
///
/// [code] 是穩定識別碼，UX 分支只能依它判斷；[message] 可能調整，僅供顯示。
class ApiException implements Exception {
  const ApiException({
    required this.statusCode,
    required this.code,
    required this.message,
  });

  final int statusCode;
  final String code;
  final String message;

  /// 後端仍在處理同一個 client_request_id。應以**相同** ID 重試，不可換新 ID。
  bool get isRequestInProgress => code == 'REQUEST_IN_PROGRESS';

  /// 同一個 client_request_id 搭配了不同內容。必須改用新的 ID。
  bool get isIdempotencyConflict => code == 'IDEMPOTENCY_CONFLICT';

  /// 音訊超過 60 秒；重錄較短的一句才有意義。
  bool get isAudioTooLong => code == 'AUDIO_TOO_LONG';

  @override
  String toString() => 'ApiException($statusCode, $code): $message';
}

/// 網路層或回應格式問題（無法解析成 api.md 的錯誤結構）。
class ApiTransportException implements Exception {
  const ApiTransportException(this.message);

  final String message;

  @override
  String toString() => 'ApiTransportException: $message';
}

/// 取得 Cognito ID Token 的回呼。由 AuthService 提供，並負責自動更新。
typedef IdTokenProvider = Future<String> Function();

class ApiClient {
  ApiClient({
    required this.baseUrl,
    required this.idTokenProvider,
    http.Client? httpClient,
  }) : _http = httpClient ?? http.Client();

  /// API Gateway 的路徑前綴，含 `/v1`。例：`https://xxx.execute-api.../v1`
  final String baseUrl;
  final IdTokenProvider idTokenProvider;
  final http.Client _http;

  /// `POST /chat` 的等待上限。後端一次 turn 要做 ASR、對話推導與 TTS，
  /// 因此比一般讀取端點寬鬆。
  static const Duration chatTimeout = Duration(seconds: 45);

  /// 送出一句長者輸入並取得 AI 回覆。[text] 與 [audio] 必須擇一。
  ///
  /// [clientRequestId] 是冪等鍵：
  /// - **新的一句話**：省略，讓本方法產生新 ID。
  /// - **重送同一句話**（連線中斷、收到 409 `REQUEST_IN_PROGRESS`）：必須帶回
  ///   原本的 ID。換新 ID 會讓後端當成新的一句，可能重複建立行程或事件。
  ///
  /// 第一輪省略 [sessionId]；之後帶回上一輪回應的 [ChatResult.sessionId]。
  Future<ChatResult> chat({
    required String elderId,
    required ChatLanguage lang,
    String? text,
    AudioInput? audio,
    String? sessionId,
    String? clientRequestId,
  }) async {
    if ((text == null) == (audio == null)) {
      throw ArgumentError('text 與 audio 必須擇一填寫，不可同時提供或同時省略');
    }

    final body = <String, Object?>{
      'client_request_id': clientRequestId ?? newClientRequestId(),
      'elder_id': elderId,
      'lang': lang.wireValue,
      if (sessionId != null) 'session_id': sessionId,
      if (text != null) 'text': text,
      if (audio != null) 'audio': audio.toJson(),
    };

    final json = await _post('/chat', body, timeout: chatTimeout);
    return ChatResult.fromJson(json);
  }

  /// 關閉 session：停止追加 turn 並啟動離線事件整理。
  ///
  /// 以 session 狀態保證冪等，因此不需要 client_request_id；收到 409
  /// `REQUEST_IN_PROGRESS` 時重試同一個呼叫即可。
  Future<Map<String, Object?>> closeChatSession(String sessionId) {
    return _post('/chat/sessions/$sessionId/close', const <String, Object?>{});
  }

  /// 更新 elder profile 的語言與客語腔調；後續 turn 的 ASR/TTS 會共同採用。
  Future<ElderVoicePreferences> updateElderVoicePreferences({
    required String elderId,
    required ElderVoicePreferences preferences,
  }) async {
    final json = await _patch('/elders/$elderId', preferences.toJson());
    return ElderVoicePreferences.fromJson(json);
  }

  void close() => _http.close();

  // TODO: getElders() / getElder() / createElder() / updateElder()
  // TODO: getSummaries() / generateSummary()
  // TODO: getEvents()
  // TODO: getRoutines()（定義列表 / ?date= 當日視圖）
  // TODO: createRoutine() / updateRoutine() / completeRoutine()
  // TODO: getStats()

  Future<Map<String, Object?>> _post(
    String path,
    Map<String, Object?> body, {
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final token = await idTokenProvider();
    final http.Response response;
    try {
      response = await _http
          .post(
            Uri.parse('$baseUrl$path'),
            headers: {
              'Authorization': 'Bearer $token',
              'Content-Type': 'application/json; charset=utf-8',
            },
            body: jsonEncode(body),
          )
          .timeout(timeout);
    } on Exception catch (error) {
      throw ApiTransportException('呼叫 $path 失敗：$error');
    }

    return _decode(response, path);
  }

  Future<Map<String, Object?>> _patch(
    String path,
    Map<String, Object?> body, {
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final token = await idTokenProvider();
    final http.Response response;
    try {
      response = await _http
          .patch(
            Uri.parse('$baseUrl$path'),
            headers: {
              'Authorization': 'Bearer $token',
              'Content-Type': 'application/json; charset=utf-8',
            },
            body: jsonEncode(body),
          )
          .timeout(timeout);
    } on Exception catch (error) {
      throw ApiTransportException('呼叫 $path 失敗：$error');
    }
    return _decode(response, path);
  }

  /// 解析回應；非 2xx 一律轉成 [ApiException]。
  Map<String, Object?> _decode(http.Response response, String path) {
    // 後端一律 UTF-8；不用 response.body 是因為它依 Content-Type 猜編碼，
    // 缺 charset 時會把中文解成亂碼。
    final raw = utf8.decode(response.bodyBytes, allowMalformed: true);

    Object? decoded;
    if (raw.isNotEmpty) {
      try {
        decoded = jsonDecode(raw);
      } on FormatException {
        decoded = null;
      }
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      if (decoded is Map<String, Object?>) {
        return decoded;
      }
      throw ApiTransportException('$path 回應不是 JSON 物件');
    }

    // 401 由 API Gateway 回應，格式與 api.md 的錯誤結構不同，因此可能取不到 code。
    var code = 'UNKNOWN_ERROR';
    var message = raw.isEmpty ? 'HTTP ${response.statusCode}' : raw;
    if (decoded is Map<String, Object?>) {
      final error = decoded['error'];
      if (error is Map<String, Object?>) {
        code = error['code'] as String? ?? code;
        message = error['message'] as String? ?? message;
      }
    }
    if (response.statusCode == 401) {
      code = 'UNAUTHORIZED';
    }

    throw ApiException(
      statusCode: response.statusCode,
      code: code,
      message: message,
    );
  }
}

/// 產生 client_request_id 用的 UUID v4。
///
/// 自己實作而不引入 uuid 套件：只需要一個隨機識別碼，不值得為此增加依賴。
String newClientRequestId() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  // version 4、variant 10xx
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}
