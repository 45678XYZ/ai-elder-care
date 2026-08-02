/// 非同步 TTS 的就緒判定。
///
/// 後端合成是非同步的，`/chat` 回來時音訊通常還不存在。這裡鎖住兩件容易寫錯、
/// 而且錯了會讓長輩「永遠聽不到後端聲音」的事：
///
/// 1. `reply_audio_status` 的解析，尤其是未知值必須退成 unavailable 而不是 pending，
///    否則畫面會對著一個永遠不會就緒的網址空等。
/// 2. 探測用的是 range GET 而非 HEAD。presigned URL 的簽章包含 HTTP method，
///    對為 GET 簽的網址打 HEAD 一律 403，等於永遠等不到。
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:e_hakka_care/shared/models/chat_reply.dart';
import 'package:e_hakka_care/shared/services/audio_service.dart';

const _url = 'https://s3.example.invalid/tts/cnv_1.mp3?X-Amz-Signature=abc';

void main() {
  group('ChatReply.replyAudioStatus', () {
    ChatReply parse(Map<String, dynamic> extra) => ChatReply.fromJson({
          'conversation_id': 'cnv_1',
          'session_id': 'ses_1',
          'transcript': '我吃過藥了',
          'reply_text': '有按時吃藥真棒！',
          'reply_audio_url': _url,
          'routines_updated': false,
          ...extra,
        });

    test('三種已知值各自對應', () {
      expect(parse({'reply_audio_status': 'pending'}).replyAudioStatus,
          ChatAudioStatus.pending);
      expect(parse({'reply_audio_status': 'ready'}).replyAudioStatus,
          ChatAudioStatus.ready);
      expect(parse({'reply_audio_status': 'unavailable'}).replyAudioStatus,
          ChatAudioStatus.unavailable);
    });

    test('缺欄位或未知值退成 unavailable，不讓畫面空等', () {
      expect(parse({}).replyAudioStatus, ChatAudioStatus.unavailable);
      expect(parse({'reply_audio_status': 'weird'}).replyAudioStatus,
          ChatAudioStatus.unavailable);
    });
  });

  group('waitForAudioReady', () {
    test('用 range GET 探測，不用 HEAD', () async {
      final methods = <String>[];
      final ready = await waitForAudioReady(
        _url,
        timeout: const Duration(seconds: 1),
        client: MockClient((request) async {
          methods.add(request.method);
          return http.Response('', 206);
        }),
      );

      expect(ready, isTrue);
      // HEAD 會被 S3 以簽章不符擋掉，用了就永遠等不到
      expect(methods, ['GET']);
    });

    test('物件還沒生出來時持續重試，直到就緒', () async {
      var calls = 0;
      final ready = await waitForAudioReady(
        _url,
        timeout: const Duration(seconds: 5),
        interval: const Duration(milliseconds: 10),
        client: MockClient((request) async {
          calls += 1;
          return http.Response('', calls < 3 ? 404 : 206);
        }),
      );

      expect(ready, isTrue);
      expect(calls, 3);
    });

    test('逾時回 false，讓呼叫端改用裝置端 TTS', () async {
      final ready = await waitForAudioReady(
        _url,
        timeout: const Duration(milliseconds: 60),
        interval: const Duration(milliseconds: 10),
        client: MockClient((request) async => http.Response('', 404)),
      );

      expect(ready, isFalse);
    });

    test('網路錯誤不會炸出來，只是當成還沒好', () async {
      final ready = await waitForAudioReady(
        _url,
        timeout: const Duration(milliseconds: 60),
        interval: const Duration(milliseconds: 10),
        client: MockClient((request) async => throw const _NetworkDown()),
      );

      expect(ready, isFalse);
    });

    test('空網址直接回 false，不打任何請求', () async {
      var calls = 0;
      expect(
        await waitForAudioReady(
          '',
          timeout: const Duration(seconds: 1),
          client: MockClient((request) async {
            calls += 1;
            return http.Response('', 206);
          }),
        ),
        isFalse,
      );
      expect(calls, 0);
    });
  });
}

class _NetworkDown implements Exception {
  const _NetworkDown();
}
