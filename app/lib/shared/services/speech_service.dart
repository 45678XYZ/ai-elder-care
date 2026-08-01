import 'package:speech_to_text/speech_to_text.dart';

/// 裝置端語音辨識（speech_to_text，底層走 Android 系統語音服務）。
///
/// 華語（zh-TW）在裝置端辨識成文字後，由 App 以 text 送 `/ask`（現在）或 `/chat`（之後）。
/// 客語（第二階段）裝置端辨識不支援，改走錄音 → audio 送後端辨識，不經此服務。
/// 含靜音自動斷句（[pauseFor]），支撐免手持對話迴圈。
class SpeechService {
  final SpeechToText _speech = SpeechToText();

  bool _available = false;

  /// 解析出的華語 localeId（不同裝置可能是 `zh-TW` 或 `cmn-Hant-TW`），找不到才退回預設。
  String _localeId = 'zh-TW';

  bool get isAvailable => _available;
  bool get isListening => _speech.isListening;

  /// 初始化並觸發麥克風權限請求；回傳裝置是否可辨識。
  ///
  /// [onStatus] 回報狀態字串（`listening`／`notListening`／`done`），供 UI 與迴圈判斷；
  /// [onError] 回報辨識錯誤訊息。
  Future<bool> init({
    void Function(String status)? onStatus,
    void Function(String error)? onError,
  }) async {
    _available = await _speech.initialize(
      onStatus: (s) => onStatus?.call(s),
      onError: (e) => onError?.call(e.errorMsg),
    );
    if (_available) {
      _localeId = await _resolveZhTwLocale();
    }
    return _available;
  }

  /// 開始聆聽一句話。辨識出最終結果（靜音斷句或使用者停止）時，以 isFinal=true 回呼。
  Future<void> listen({
    required void Function(String text, bool isFinal) onResult,
    Duration listenFor = const Duration(seconds: 30),
    Duration pauseFor = const Duration(seconds: 3),
  }) async {
    if (!_available) return;
    await _speech.listen(
      onResult: (r) => onResult(r.recognizedWords, r.finalResult),
      listenOptions: SpeechListenOptions(
        partialResults: true,
        listenMode: ListenMode.dictation,
        cancelOnError: true,
        localeId: _localeId,
        listenFor: listenFor,
        pauseFor: pauseFor,
      ),
    );
  }

  /// 停止聆聽，保留已辨識結果（會觸發最終結果回呼）。
  Future<void> stop() => _speech.stop();

  /// 取消聆聽，丟棄結果。
  Future<void> cancel() => _speech.cancel();

  /// 從系統語言清單挑出華語（台灣）的 localeId；挑不到就退回 `zh-TW`。
  Future<String> _resolveZhTwLocale() async {
    final locales = await _speech.locales();
    for (final l in locales) {
      final id = l.localeId.toLowerCase();
      if (id.contains('tw') && (id.contains('zh') || id.contains('cmn'))) {
        return l.localeId;
      }
    }
    return 'zh-TW';
  }
}
