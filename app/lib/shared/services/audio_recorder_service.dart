import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// 錄下長者說的一句話，交給 [ApiClient.chat] 的 audio 參數送後端辨識（STT）。
///
/// 對應 docs/api.md 的 `/chat` audio 路線：客語（`hak`）裝置端無法辨識，改錄音送後端；
/// 華語也可走此路線讓後端統一辨識。錄成 m4a（AAC-LC）。
///
/// 單句上限 60 秒（[maxDuration]）：超過後端回 400 `AUDIO_TOO_LONG`，所以這裡到點就
/// 自行收音，不讓長者講完一大段才被退。
///
/// **講完就送靠靜音偵測**（[start] 的 [AudioRecorderStopReason.silence]）。這一段是自己
/// 做的：華語走裝置端 ASR，斷句由 `speech_to_text` 的 `pauseFor` 內建處理；錄音這條
/// 沒有人給，只有 60 秒上限的話，長輩講完一句要乾等將近一分鐘，免手持迴圈等於壞掉。
class AudioRecorderService {
  /// [recorder] 與 [tempDirPath] 可注入，讓靜音偵測測得起來——測試餵一串音量進來，
  /// 就能驗「句中停頓不會被切斷」這種行為，不必真的錄音，也不必碰 platform channel。
  AudioRecorderService({
    AudioRecorder? recorder,
    Future<String> Function()? tempDirPath,
  })  : _recorder = recorder ?? AudioRecorder(),
        _tempDirPath =
            tempDirPath ?? (() async => (await getTemporaryDirectory()).path);

  final AudioRecorder _recorder;
  final Future<String> Function() _tempDirPath;

  /// 單句音檔上限，對齊 docs/api.md 的 `audio.data`（超過後端回 400 `AUDIO_TOO_LONG`）。
  ///
  /// 這是**上限保護**，不是斷句：靠它收音的話，長輩講完一句話要乾等將近一分鐘。
  /// 講完就送是 [_silenceThresholdDb] 那組參數在管。
  static const Duration maxDuration = Duration(seconds: 60);

  // ---- 靜音偵測參數 ----
  //
  // ⚠️ 這三個數字**是猜的**，安靜房間與吵雜會場的表現完全不同，要在實機上調。
  // 文件沒有規定怎麼做斷句——framework.md 只要求「免手持迴圈」，而華語那條是
  // speech_to_text 內建的 pauseFor 免費給的，錄音這條沒有人給，只能自己做。

  /// 低於這個音量（dBFS，0 最大、負得愈多愈安靜）就算沒在講話。
  static const double _silenceThresholdDb = -40;

  /// 講完之後要靜多久才算一句話結束。
  ///
  /// **對齊華語那條的 `SpeechService.pauseFor`（3 秒）**，不是隨便取的數字：長輩
  /// 句中停頓本來就長（想詞、喘氣、被別的事打斷），設太短會把話從中間切掉，而
  /// 「AI 對著半句話回答」對長輩是很難理解的——他不會知道是自己被打斷了。
  ///
  /// 兩條路用同一個數字還有一個理由：同一位長輩換個語言就被切斷，是最糟的不一致。
  ///
  /// 切太早與切太晚的代價不對稱——切晚只是多等一下，切早會讓那句話白講，
  /// 所以拿不準時一律往長的調。
  static const Duration _silenceToStop = Duration(seconds: 3);

  /// 點了麥克風卻一直沒開口，等這麼久就收掉。
  ///
  /// 沒有這個保護的話，長輩按了不講話會一路錄到 [maxDuration]——畫面上就是
  /// 「我在聽」卡住一分鐘。
  static const Duration _noSpeechTimeout = Duration(seconds: 8);

  /// 多久讀一次音量。太密會吃電，太疏會讓斷句延遲變得明顯。
  static const Duration _amplitudePollInterval = Duration(milliseconds: 200);

  Timer? _limitTimer;
  Timer? _amplitudeTimer;
  DateTime? _startedAt;

  /// 是否曾經聽到有人講話。
  ///
  /// **非有不可**：長輩點了麥克風還在想要說什麼，開頭本來就是安靜的。沒有這個旗標，
  /// 靜音計時從第一秒就開始跑，1.5 秒後就把一段空白音檔送出去了。
  bool _heardSpeech = false;

  /// 連續靜音累積了多久（只在 [_heardSpeech] 之後才累計）。
  Duration _silentFor = Duration.zero;

  /// 到點自動收音時先收著的結果，等呼叫端的 [stop] 來領。
  String? _autoStopped;

  /// 是否已取得麥克風權限；首次呼叫會觸發系統權限請求。
  Future<bool> hasPermission() => _recorder.hasPermission();

  Future<bool> get isRecording => _recorder.isRecording();

  /// 已錄多久；沒在錄時為零。UI 可據此顯示剩餘秒數。
  Duration get elapsed => _startedAt == null
      ? Duration.zero
      : DateTime.now().difference(_startedAt!);

  /// 距離上限還剩多久；已到點為零。
  Duration get remaining {
    final left = maxDuration - elapsed;
    return left.isNegative ? Duration.zero : left;
  }

  /// 開始錄音；沒有麥克風權限時回傳 false、不錄。
  ///
  /// 三種情況會自動收音並呼叫 [onDone]，讓 UI 收掉聆聽狀態：
  /// - 講完了（靜音超過 [_silenceToStop]）——這是免手持迴圈實際靠的那條
  /// - 一直沒開口（[_noSpeechTimeout]）
  /// - 錄滿 [maxDuration]
  ///
  /// 三種都走同一個回呼、同一條收音路徑：呼叫端收到通知後照常 `await stop()` 即可
  /// 拿到音檔，不必分開處理。[AudioRecorderStopReason] 只用來決定要說什麼話
  /// （沒聽到聲音時該提示長輩再說一次，講完了則不必多說）。
  Future<bool> start({
    void Function(AudioRecorderStopReason reason)? onDone,
  }) async {
    if (!await _recorder.hasPermission()) return false;
    final dir = await _tempDirPath();
    final path = '$dir/chat_${DateTime.now().millisecondsSinceEpoch}.m4a';
    await _recorder.start(
      const RecordConfig(encoder: AudioEncoder.aacLc),
      path: path,
    );

    _autoStopped = null;
    _startedAt = DateTime.now();
    _heardSpeech = false;
    _silentFor = Duration.zero;

    _limitTimer?.cancel();
    _limitTimer = Timer(
      maxDuration,
      () => _autoStop(AudioRecorderStopReason.maxDuration, onDone),
    );

    _amplitudeTimer?.cancel();
    _amplitudeTimer =
        Timer.periodic(_amplitudePollInterval, (_) => _tick(onDone));
    return true;
  }

  /// 讀一次音量，決定要不要收音。
  ///
  /// 拆成獨立方法是為了測得到：測試注入假的 [AudioRecorder]，餵一串音量進來，
  /// 就能驗「講話中不會被切斷」「靜音夠久才停」這些行為，不必真的錄音。
  Future<void> _tick(void Function(AudioRecorderStopReason)? onDone) async {
    if (_startedAt == null) return; // 已經收過音了

    final double db;
    try {
      db = (await _recorder.getAmplitude()).current;
    } catch (_) {
      // 讀不到音量就放棄靜音偵測，交給 [maxDuration] 兜底。多錄一點總比
      // 因為讀不到音量就把長輩的話切掉好。
      _amplitudeTimer?.cancel();
      _amplitudeTimer = null;
      return;
    }

    if (db > _silenceThresholdDb) {
      _heardSpeech = true;
      _silentFor = Duration.zero;
      return;
    }

    // 累計靜音時間用「輪詢了幾次」而不是牆上時鐘：這個決定本來就是輪詢驅動的，
    // 讀 DateTime.now() 等於混進第二個時間來源，兩者不同步時行為會很難解釋
    // （測試裡更是直接失效——假時鐘推得動計時器，推不動 DateTime.now()）。
    _silentFor += _amplitudePollInterval;

    // 還沒開口：等 [_noSpeechTimeout] 就收掉，不要卡在「我在聽」到一分鐘。
    if (!_heardSpeech) {
      if (_silentFor >= _noSpeechTimeout) {
        await _autoStop(AudioRecorderStopReason.noSpeech, onDone);
      }
      return;
    }

    if (_silentFor >= _silenceToStop) {
      await _autoStop(AudioRecorderStopReason.silence, onDone);
    }
  }

  /// 自動收音：把音檔先收著等呼叫端來領，然後通知 UI。
  Future<void> _autoStop(
    AudioRecorderStopReason reason,
    void Function(AudioRecorderStopReason)? onDone,
  ) async {
    _limitTimer?.cancel();
    _limitTimer = null;
    _amplitudeTimer?.cancel();
    _amplitudeTimer = null;
    // 先清 _startedAt：收音是 async 的，這段期間 [_tick] 可能又被觸發一次，
    // 那會對已經停掉的 recorder 再 stop 一次，第二次拿到 null 把音檔蓋掉。
    _startedAt = null;
    _autoStopped = await _readAndDelete(await _recorder.stop());
    onDone?.call(reason);
  }

  /// 停止錄音，回傳 base64 音檔（可直接餵給 `chat(audioBase64: ...)`）；沒錄到內容回 null。
  Future<String?> stop() async {
    _stopTimers();

    // 已經自動收過音了（講完、沒開口或到上限），把那份交出去
    // （重複呼叫 recorder.stop() 只會拿到 null）。
    final auto = _autoStopped;
    if (auto != null) {
      _autoStopped = null;
      return auto;
    }
    return _readAndDelete(await _recorder.stop());
  }

  /// 取消錄音並丟棄音檔（使用者中途放棄時用）。
  Future<void> cancel() async {
    _stopTimers();
    _autoStopped = null;
    await _recorder.cancel();
  }

  void _stopTimers() {
    _limitTimer?.cancel();
    _limitTimer = null;
    _amplitudeTimer?.cancel();
    _amplitudeTimer = null;
    _startedAt = null;
  }

  /// 讀出音檔轉 base64，讀完即刪本地暫存——語音屬個資（見 docs/pii.md），不留在裝置上。
  Future<String?> _readAndDelete(String? path) async {
    if (path == null) return null;
    final file = File(path);
    if (!await file.exists()) return null;
    final bytes = await file.readAsBytes();
    await file.delete();
    return bytes.isEmpty ? null : base64Encode(bytes);
  }

  void dispose() {
    _stopTimers();
    _recorder.dispose();
  }
}

/// 錄音為什麼停下來。決定 UI 要不要多說一句話。
enum AudioRecorderStopReason {
  /// 講完了（靜音夠久）。免手持迴圈正常的那條路，不必對長輩多說什麼。
  silence,

  /// 點了麥克風但一直沒開口。要提示長輩再說一次，否則他不知道發生什麼事。
  noSpeech,

  /// 錄滿單句上限（60 秒，api.md）。已經錄到的照樣送出，不要整段丟掉。
  maxDuration,
}
