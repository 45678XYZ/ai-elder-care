import 'package:flutter_tts/flutter_tts.dart';
import 'package:just_audio/just_audio.dart';

/// AI 回覆語音播放。
///
/// 兩條路，兩者都等到播完才回傳，讓對話迴圈在播完後再接續聆聽：
/// - [speak]：裝置端 TTS（flutter_tts，zh-TW、放慢語速）唸出回覆文字。第一版華語迴圈用，
///   正式 `/chat` 未上線前的臨時方案。
/// - [playUrl]：播正式 `/chat` 回傳的 `reply_audio_url`（Polly 合成、S3 presigned）。
class AudioService {
  final FlutterTts _tts = FlutterTts();
  final AudioPlayer _player = AudioPlayer();

  bool _inited = false;

  Future<void> _ensureInit() async {
    if (_inited) return;
    await _tts.setLanguage('zh-TW');
    await _tts.setSpeechRate(0.45); // 0~1，放慢方便長輩聽清楚
    // 讓 speak() 等到整段唸完才回傳，對話迴圈才能在唸完後再聆聽。
    await _tts.awaitSpeakCompletion(true);
    _inited = true;
  }

  /// 唸出文字，等到唸完才回傳。空字串直接略過。
  Future<void> speak(String text) async {
    if (text.trim().isEmpty) return;
    await _ensureInit();
    await _tts.speak(text);
  }

  /// 播放後端回傳的 `reply_audio_url`，等到播完才回傳。空網址直接略過。
  ///
  /// presigned URL 有時效（見 docs/api.md），拿到後應盡快播；載入或播放失敗會丟例外，
  /// 由呼叫端接住。`play()` 的 Future 會等到播放結束才 complete。
  Future<void> playUrl(String url) async {
    if (url.isEmpty) return;
    await _player.stop();
    await _player.setUrl(url);
    await _player.play();
  }

  /// 中斷目前播放（TTS 與 URL 播放都停）。
  Future<void> stop() async {
    await _tts.stop();
    await _player.stop();
  }

  void dispose() {
    _tts.stop();
    _player.dispose();
  }
}
