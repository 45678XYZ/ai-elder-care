import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;
import 'package:just_audio/just_audio.dart';

/// AI 回覆語音播放。
///
/// 兩條路，兩者都等到播完才回傳，讓對話迴圈在播完後再接續聆聽：
/// - [speak]：裝置端 TTS（flutter_tts，zh-TW、放慢語速）唸出回覆文字。第一版華語迴圈用，
///   正式 `/chat` 未上線前的臨時方案。
/// - [playUrl]：播正式 `/chat` 回傳的 `reply_audio_url`（S3 presigned）。
///
/// 後端合成是非同步的，`/chat` 回來時音訊通常還不存在（`reply_audio_status=pending`），
/// 要先用 [waitUntilReady] 等它出現才播得出來。
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

  /// 等待非同步合成的音訊出現；就緒回 true，逾時或出錯回 false。
  ///
  /// 實作在 [waitForAudioReady]——那支不碰播放器與 TTS，才能單獨測試。
  Future<bool> waitUntilReady(
    String url, {
    required Duration timeout,
    Duration interval = const Duration(seconds: 2),
  }) =>
      waitForAudioReady(url, timeout: timeout, interval: interval);

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


/// 等待 S3 上的非同步合成音訊出現；就緒回 true，逾時或出錯回 false。
///
/// 刻意獨立於 [AudioService]：那個類別在建構時就會建立 `FlutterTts` 與 `AudioPlayer`，
/// 需要 platform channel，測不到這段純網路邏輯。
///
/// 用「只要第一個 byte」的 range GET 探測，不用 HEAD 也不反覆 `setUrl`：
///
/// - HEAD 不行。presigned URL 的簽章包含 HTTP method，拿為 GET 簽的網址打 HEAD
///   一律回 403，永遠等不到就緒（實測確認）。
/// - 反覆 `setUrl` 也不好：每次失敗都要建一次播放器管線，成本高又會在平台層留下
///   錯誤 log。
///
/// 物件已存在回 206（range 命中），還沒生出來回 404。
///
/// [timeout] 是硬上限。長輩在這段時間裡是對著安靜的畫面等，等太久跟當機沒兩樣，
/// 因此呼叫端逾時後必須改用裝置端 TTS，而不是繼續等下去。
Future<bool> waitForAudioReady(
  String url, {
  required Duration timeout,
  Duration interval = const Duration(seconds: 2),
  http.Client? client,
}) async {
  if (url.isEmpty) return false;
  final uri = Uri.tryParse(url);
  if (uri == null) return false;

  final httpClient = client ?? http.Client();
  final deadline = DateTime.now().add(timeout);
  try {
    while (DateTime.now().isBefore(deadline)) {
      try {
        final response = await httpClient
            .get(uri, headers: const {'Range': 'bytes=0-0'})
            .timeout(const Duration(seconds: 5));
        // 206 是 range 命中；200 是伺服器忽略 range 直接整份回來，兩者都代表已就緒。
        if (response.statusCode == 206 || response.statusCode == 200) {
          return true;
        }
      } catch (_) {
        // 網路瞬斷或逾時：當成還沒好，下一輪再試。
      }
      final remaining = deadline.difference(DateTime.now());
      if (remaining <= Duration.zero) break;
      await Future<void>.delayed(remaining < interval ? remaining : interval);
    }
    return false;
  } finally {
    // 自己開的才自己關；呼叫端傳進來的由它自己管理生命週期。
    if (client == null) httpClient.close();
  }
}
