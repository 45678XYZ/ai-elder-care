import 'dart:convert';

import 'package:ai_elder_care/shared/services/api_client.dart';
import 'package:ai_elder_care/shared/services/api_exception.dart';
import 'package:ai_elder_care/shared/services/chat_session.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// 測試用的假後端：記錄每次收到的請求，並依序回傳預先排好的回應。
class _FakeBackend {
  _FakeBackend(this._responses);

  /// 每次呼叫要回的東西：http.Response，或要丟出的例外。
  final List<Object> _responses;

  final List<http.Request> requests = [];
  int _i = 0;

  http.Client get client => MockClient((req) async {
        requests.add(req);
        final next =
            _responses[_i < _responses.length ? _i : _responses.length - 1];
        _i++;
        if (next is Exception) throw next;
        return next as http.Response;
      });

  Map<String, dynamic> bodyOf(int i) =>
      jsonDecode(requests[i].body) as Map<String, dynamic>;
}

http.Response _chatOk({
  required String sessionId,
  String conversationId = 'cnv_1',
}) =>
    http.Response(
      jsonEncode({
        'conversation_id': conversationId,
        'session_id': sessionId,
        'transcript': '我吃過藥了',
        'reply_text': '有按時吃藥真棒！',
        'reply_audio_url': 'https://example.invalid/a.mp3',
        'routines_updated': false,
      }),
      200,
      headers: {'content-type': 'application/json; charset=utf-8'},
    );

http.Response _error(int status, String code) => http.Response(
      jsonEncode({
        'error': {'code': code, 'message': '測試錯誤'}
      }),
      status,
      headers: {'content-type': 'application/json; charset=utf-8'},
    );

ChatSession _session(_FakeBackend backend) => ChatSession(
      api: ApiClient(baseUrl: 'http://test', httpClient: backend.client),
      elderId: 'eld_a1b2c3d4e5f6',
      lang: 'zh-TW',
      retryBaseDelay: Duration.zero,
    );

void main() {
  group('session_id 生命週期', () {
    test('第一輪不帶 session_id，之後帶回後端給的值', () async {
      final backend = _FakeBackend([
        _chatOk(sessionId: 'ses_A'),
        _chatOk(sessionId: 'ses_A'),
      ]);
      final chat = _session(backend);

      await chat.send(text: '第一句');
      await chat.send(text: '第二句');

      expect(backend.bodyOf(0).containsKey('session_id'), isFalse);
      expect(backend.bodyOf(1)['session_id'], 'ses_A');
      expect(chat.sessionId, 'ses_A');
    });

    test('後端換新 session 時以回應為準覆蓋本地值', () async {
      final backend = _FakeBackend([
        _chatOk(sessionId: 'ses_A'),
        // 原 session 已 idle／關閉，後端改用新的。
        _chatOk(sessionId: 'ses_B'),
        _chatOk(sessionId: 'ses_B'),
      ]);
      final chat = _session(backend);

      await chat.send(text: '第一句');
      await chat.send(text: '第二句');
      await chat.send(text: '第三句');

      expect(backend.bodyOf(2)['session_id'], 'ses_B');
      expect(chat.sessionId, 'ses_B');
    });
  });

  group('client_request_id 冪等鍵', () {
    test('每句話一個新的 ID', () async {
      final backend = _FakeBackend([
        _chatOk(sessionId: 'ses_A'),
        _chatOk(sessionId: 'ses_A'),
      ]);
      final chat = _session(backend);

      await chat.send(text: '第一句');
      await chat.send(text: '第二句');

      expect(
        backend.bodyOf(0)['client_request_id'],
        isNot(backend.bodyOf(1)['client_request_id']),
      );
    });

    test('自動重試沿用同一個 ID', () async {
      final backend = _FakeBackend([
        _error(409, 'REQUEST_IN_PROGRESS'),
        _error(500, 'INTERNAL_ERROR'),
        _chatOk(sessionId: 'ses_A'),
      ]);
      final chat = _session(backend);

      await chat.send(text: '我吃過藥了');

      expect(backend.requests, hasLength(3));
      final id = backend.bodyOf(0)['client_request_id'];
      expect(backend.bodyOf(1)['client_request_id'], id);
      expect(backend.bodyOf(2)['client_request_id'], id);
    });

    test('重試耗盡後重送同一句話，仍沿用原 ID（避免重複副作用）', () async {
      final backend = _FakeBackend([_error(500, 'INTERNAL_ERROR')]);
      final chat = _session(backend);

      await expectLater(
        chat.send(text: '我吃過藥了'),
        throwsA(isA<ApiException>()),
      );
      final idBefore = backend.bodyOf(0)['client_request_id'];

      await expectLater(
        chat.send(text: '我吃過藥了'),
        throwsA(isA<ApiException>()),
      );

      expect(backend.bodyOf(backend.requests.length - 1)['client_request_id'],
          idBefore);
    });

    test('冪等衝突不重試（呼叫端的 bug，重送無用）', () async {
      final backend = _FakeBackend([_error(409, 'IDEMPOTENCY_CONFLICT')]);
      final chat = _session(backend);

      await expectLater(
        chat.send(text: '我吃過藥了'),
        throwsA(isA<ApiException>().having(
            (e) => e.isIdempotencyConflict, 'isIdempotencyConflict', isTrue)),
      );
      expect(backend.requests, hasLength(1));
    });
  });

  group('close', () {
    test('沒有 session 時不打 API', () async {
      final backend = _FakeBackend([_chatOk(sessionId: 'ses_A')]);
      final chat = _session(backend);

      expect(await chat.close(), isNull);
      expect(backend.requests, isEmpty);
    });

    test('關閉後清掉本地 session，body 為空物件', () async {
      final backend = _FakeBackend([
        _chatOk(sessionId: 'ses_A'),
        http.Response(
          jsonEncode({
            'session_id': 'ses_A',
            'status': 'closed',
            'closed_at': '2026-07-14T09:20:00+08:00',
            'batch_status': 'pending',
          }),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        ),
      ]);
      final chat = _session(backend);

      await chat.send(text: '我吃過藥了');
      final result = await chat.close();

      expect(backend.requests[1].url.path, '/v1/chat/sessions/ses_A/close');
      expect(backend.bodyOf(1), isEmpty);
      expect(result?.batchStatus, 'pending');
      expect(chat.sessionId, isNull);
    });

    test('仍有 turn 在處理時退避重試同一個呼叫', () async {
      final backend = _FakeBackend([
        _chatOk(sessionId: 'ses_A'),
        _error(409, 'REQUEST_IN_PROGRESS'),
        http.Response(
          jsonEncode({
            'session_id': 'ses_A',
            'status': 'closed',
            'closed_at': '2026-07-14T09:20:00+08:00',
            'batch_status': 'pending',
          }),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        ),
      ]);
      final chat = _session(backend);

      await chat.send(text: '我吃過藥了');
      final result = await chat.close();

      expect(backend.requests, hasLength(3));
      expect(result?.status, 'closed');
    });

    test('關不掉也不丟例外（交給後端 idle closer 收斂）', () async {
      final backend = _FakeBackend([
        _chatOk(sessionId: 'ses_A'),
        _error(500, 'INTERNAL_ERROR'),
      ]);
      final chat = _session(backend);

      await chat.send(text: '我吃過藥了');

      expect(await chat.close(), isNull);
      expect(chat.sessionId, isNull);
    });
  });
}
