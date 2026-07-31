import 'dart:convert';

import 'package:ai_elder_care/shared/services/api_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('ChatResult 接受沒有 TTS 音訊的成功回應', () {
    final result = ChatResult.fromJson({
      'conversation_id': 'cnv_1',
      'session_id': 'ses_1',
      'transcript': '食飽吂？',
      'reply_text': '食飽咧。',
      'reply_audio_url': null,
      'routines_updated': false,
    });

    expect(result.replyAudioUrl, isNull);
    expect(result.hasReplyAudio, isFalse);
  });

  test('裝置 TTS fallback 只接受精確支援的 locale', () {
    expect(
      ChatLanguage.zhTw.canUseDeviceTtsFallback(['en-US', 'zh_TW']),
      isTrue,
    );
    expect(
      ChatLanguage.hak.canUseDeviceTtsFallback(['zh-TW', 'zh-CN']),
      isFalse,
    );
  });

  test('更新語音偏好會送出六腔 wire value', () async {
    late Map<String, Object?> sentBody;
    final client = ApiClient(
      baseUrl: 'https://example.test/v1',
      idTokenProvider: () async => 'token',
      httpClient: MockClient((request) async {
        expect(request.method, 'PATCH');
        expect(request.url.path, '/v1/elders/eld_1');
        sentBody = jsonDecode(request.body) as Map<String, Object?>;
        return http.Response(
          jsonEncode({
            'lang_preference': 'hak',
            'hakka_dialect': 'htia_hailu',
          }),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );

    final result = await client.updateElderVoicePreferences(
      elderId: 'eld_1',
      preferences: const ElderVoicePreferences(
        language: ChatLanguage.hak,
        hakkaDialect: HakkaDialect.hailu,
      ),
    );

    expect(sentBody['lang_preference'], 'hak');
    expect(sentBody['hakka_dialect'], 'htia_hailu');
    expect(result.hakkaDialect, HakkaDialect.hailu);
  });
}
