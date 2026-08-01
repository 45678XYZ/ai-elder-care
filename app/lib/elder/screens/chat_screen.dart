import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../shared/i18n/strings.dart';
import '../../shared/models/chat_reply.dart';
import '../../shared/services/api_exception.dart';
import '../../shared/services/audio_recorder_service.dart';
import '../../shared/services/audio_service.dart';
import '../../shared/services/care_repository.dart';
import '../../shared/services/routine_sync.dart';
import '../../shared/services/session_store.dart';
import '../../shared/services/speech_service.dart';
import '../../theme/app_theme.dart';
import '../widgets/greeting_slot.dart';

/// 對話迴圈階段。
enum _Phase { idle, listening, thinking, speaking }

/// S3 `/elder/chat` — 長者模式語音陪伴主畫面。
///
/// 免手持迴圈：裝置端 ASR 聆聽（zh-TW）→ 送 `CareRepo.chat()` → 唸出回覆 →
/// 唸完自動再聆聽（見 docs/framework.md）。回覆優先播後端合成的 `reply_audio_url`，
/// 沒有才退回裝置端 TTS（見 [_speakReply]）。
///
/// 回覆帶 `routines_updated=true` 時走 [RoutineSync.refresh]：長輩用講的完成或新增行程，
/// 後端會寫進 routines，但今日畫面與本地通知是 App 自己的，不重整就看不到。
///
/// 長者規格：內文 >=24sp、觸控 >=60dp、可互動元素 <=3、語音有打字備援（§5）。
///
/// 兩種語言走的路不同：華語用裝置端辨識、送 `text`；客語裝置端 ASR 聽不懂，改錄音
/// 送 `audio` 由後端辨識（見 [_recordTurn]，api.md 兩種都收）。差別不只是資料形態
/// ——錄音那條**聆聽期間畫面上沒有逐字稿**，長輩說了什麼要等 `transcript` 回來。
class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen>
    with SingleTickerProviderStateMixin {
  final _speech = SpeechService();
  final _audio = AudioService();

  /// 客語用：裝置端 ASR 聽不懂客語，改錄音送後端辨識。
  final _recorder = AudioRecorderService();

  /// listening／speaking 的脈動外環動畫（§8：600–900ms、可被 disableAnimations 關閉）。
  late final AnimationController _pulse;

  _Phase _phase = _Phase.idle;

  /// 免手持迴圈是否開啟；為 true 時每次唸完回覆會自動再聆聽。
  bool _conversationActive = false;
  bool _micAvailable = false;

  /// 目前這一輪問答的序號。每開始一輪就 +1，[_stopConversation] 也 +1。
  ///
  /// 用來作廢「已經送出、但使用者中途按了停止」的請求：`await` 回來之後如果序號
  /// 已經不是自己那一輪，就什麼都不做。沒有這道檢查的話，按停止之後回應照樣會把
  /// 階段拉回 speaking 並唸出來——長輩按了停止，它還在講話。
  int _turnSeq = 0;

  /// 這次進入畫面後的完整對話。每一輪問答都往後加，不覆蓋前一輪——
  /// 長輩會想回頭看剛才問過什麼、AI 說過什麼，蓋掉等於對話沒發生過。
  final List<_Message> _messages = [];
  final _scrollCtrl = ScrollController();

  /// 正在辨識中、還沒定案的那一句。定案後移進 [_messages]，這裡清空。
  String _question = '';

  /// 沒聽懂時給長者的提示。
  ///
  /// 存的是**華語原文**（i18n 對照表的 key），由 [_appendHint] 在顯示前換成長輩選的
  /// 書寫語言。連續失敗時不重複加，否則長輩會被同一句洗版。
  static const _notHeardHint = '我剛剛沒聽清楚，可以再說一次，或用打字。';

  /// 客語那條在 transcript 回來之前，長輩泡泡先放這個。
  ///
  /// 用刪節號而不是留空字串：空泡泡在畫面上是一塊看不出用途的深色方塊，
  /// 「⋯」讓長輩知道那是他剛講的話、正在處理。
  static const _pendingElderBubble = '⋯';

  /// listening 已聆聽秒數。
  Timer? _listenTimer;
  int _listenSeconds = 0;

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);
    _initSpeech();
    _loadElder();
    // 長輩在今日頁換了語言之後切回來，要重問一次權限：這一頁掛在
    // StatefulNavigationShell 底下，切走再切回來 State 是留著的、initState 不會
    // 重跑，而 [_initSpeech] 華語問的是裝置端辨識、客語問的是錄音，不是同一件事。
    AppSession.langRevision.addListener(_onLangChanged);
    // 書寫語言則單純重畫就好，不必動麥克風。
    AppSession.textLangRevision.addListener(_onTextLangChanged);
  }

  void _onTextLangChanged() {
    if (mounted) setState(() {});
  }

  void _onLangChanged() {
    if (!mounted) return;
    // 講到一半換語言：先把在飛的那一輪收乾淨，否則新的輸入路徑會跟舊的搶麥克風。
    unawaited(_stopConversation());
    _initSpeech();
  }

  /// 載入長輩資料後重畫，問候語才叫得出名字而不是「阿公／阿嬤」。
  Future<void> _loadElder() async {
    await AppSession.instance.ensureEldersLoaded();
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    AppSession.langRevision.removeListener(_onLangChanged);
    AppSession.textLangRevision.removeListener(_onTextLangChanged);
    _listenTimer?.cancel();
    _silenceTimer?.cancel();
    _scrollCtrl.dispose();
    _pulse.dispose();
    _speech.cancel();
    _audio.dispose();
    // 錄到一半離開畫面：音檔要丟掉，不是留在暫存區。語音屬個資（docs/pii.md），
    // 而且那段話已經沒有人要了。
    _recorder.dispose();
    // 離開對話畫面要關 session：凍結對話快照並啟動離線事件整理（api.md）。
    // 不 await——dispose 不能是 async，而這件事本來就不必擋住畫面收掉；
    // 萬一沒送到，後端的 idle closer 最終也會收斂它。
    unawaited(CareRepo.instance.closeChat());
    super.dispose();
  }

  /// 這台裝置能不能用語音。兩種語言問的是不同的東西。
  ///
  /// 客語走錄音，所以要問**錄音**權限；問 `speech_to_text` 沒有意義——它初始化成功
  /// 只代表裝置支援語音辨識，跟能不能錄音是兩件事，而客語根本不會用到它。
  /// 反過來也一樣：華語裝置端辨識不通時，就算錄得到音也沒有用。
  Future<void> _initSpeech() async {
    var ok = false;
    try {
      ok = AppSession.instance.isHakka
          ? await _recorder.hasPermission()
          : await _speech.init(
              // 辨識失敗對長者只有一種有用的說法：沒聽清楚，再說一次或改打字。
              // 原始錯誤碼幫不上忙，但要留在歷史裡，長輩往回捲才知道哪一句沒進去。
              onError: (_) {
                if (!mounted) return;
                // 錯誤已經處理完這一輪，別讓後續的狀態回報再補救一次
                // （否則同一輪會補兩則提示、錯誤計數也多加一次）。
                _awaitingFinal = false;
                _silenceTimer?.cancel();
                _appendNotHeardHint();
                _onListenFailed();
              },
              onStatus: _onSpeechStatus,
            );
    } catch (_) {
      ok = false; // 平台不支援或測試環境無外掛，退回打字備援
    }
    if (mounted) setState(() => _micAvailable = ok);
  }

  /// 這一輪已經開始聆聽、但還沒收到最終結果。
  ///
  /// 用來分辨語音服務「正常收工」與「悄悄收工」：[_speech.listen] 有 `listenFor`
  /// 上限（30 秒），時間到了辨識器會自己停下來。**如果那段時間裡長輩一句話都沒被
  /// 收到，`onResult` 不會被呼叫、`onError` 也不會**——它只是安靜地結束。
  ///
  /// 沒有接 `onStatus` 之前，那一刻沒有任何人知道：畫面繼續顯示「我在聽」、秒數
  /// 繼續往上跳，而底下早就沒在聽了。實機看到過連續 400 秒沒有任何逐字稿。
  bool _awaitingFinal = false;

  /// 這一輪目前認定的辨識文字（見 [mergeRecognized]）。
  ///
  /// Android 的辨識器會**在同一輪裡重新分段**：分段之後 `recognizedWords` 從新的
  /// 一段從頭算起，畫面上前半句當場消失、送出去的也只剩後半句。實機是講完
  /// 「我今天 11 點要去吃午餐」之後看著逐字稿被削掉。
  String _bestHeard = '';

  /// 收到辨識文字之後的靜音收尾計時。
  ///
  /// `listen` 的 `pauseFor` 由外掛與系統語音服務共同決定，實機不保證準時觸發——
  /// 長輩講完了，畫面卻停在「我在聽」、秒數一路跳到 `listenFor` 上限（30 秒）
  /// 才收工。所以 App 這邊自己再看一次：最後一次收到文字之後靜音超過
  /// [_silenceCutoff] 就主動 stop，讓最終結果現在就出來。
  Timer? _silenceTimer;

  /// 講完之後最多等這麼久就收尾。
  ///
  /// 比 `pauseFor` 的 6 秒短，所以正常情況下由這裡決定節奏，`pauseFor` 只當外層
  /// 保險。不能再短：長輩講一句話中間本來就會停頓（想詞、換氣），切太快會把
  /// 半句話當成一句送出去。
  static const _silenceCutoff = Duration(seconds: 4);

  /// 這一輪觸發的長者檔案重讀。開下一輪聆聽之前要等它落地——
  /// 見 [_handleQuestion] 裡的說明。
  Future<void>? _profileRefresh;

  /// 語音服務的狀態回報（`listening`／`notListening`／`done`）。
  ///
  /// 只處理一件事：**聆聽結束了，但這一輪從頭到尾沒有最終結果**。那代表剛才
  /// 什麼都沒收到（沒開口、環境太吵、辨識器自己放棄），要嘛重開一輪、要嘛收手，
  /// 不能就這樣停在 listening。
  ///
  /// 走 [_onListenFailed] 與錯誤同一條路：對長輩來說「聽不到」跟「聽錯」沒有差別，
  /// 而連續失敗的收手邏輯兩邊都需要。
  void _onSpeechStatus(String status) {
    if (!mounted) return;
    final ended = status == 'done' || status == 'notListening';
    if (!ended || !_awaitingFinal) return;
    _awaitingFinal = false;
    _silenceTimer?.cancel();

    // 已經聽到內容、只是最終結果沒送來（主動 stop 之後常常是這樣：辨識器直接
    // 收工，不再補一次 isFinal）。那句話不能就這樣丟掉，直接當成定案送出——
    // 否則長輩看到自己的話出現在畫面上，然後被一句「沒聽清楚」蓋掉。
    final heard = _bestHeard.trim();
    if (heard.isNotEmpty) {
      _bestHeard = '';
      _consecutiveListenErrors = 0;
      _handleQuestion(text: heard, continueLoop: true);
      return;
    }

    _appendNotHeardHint();
    _onListenFailed();
  }

  /// 重新開始算靜音。每收到一次辨識文字就往後推。
  void _armSilenceCutoff() {
    _silenceTimer?.cancel();
    _silenceTimer = Timer(_silenceCutoff, () async {
      if (!mounted) return;
      try {
        // stop 保留已辨識的內容並觸發最終結果；cancel 會整段丟掉，不能用。
        await _speech.stop();
      } catch (_) {
        // 已經停了或平台不支援：`onStatus` 那條路會接住
      }
    });
  }

  /// 連續辨識失敗的次數。成功收到一句就歸零。
  ///
  /// Android 的語音服務在連續幾輪之後很容易開始回錯誤（辨識器忙碌、逾時、no match），
  /// 而 `cancelOnError: true` 會讓那一輪的聆聽直接取消。原本 onError 只補一句提示、
  /// 沒有把迴圈接回去，於是 `_phase` 卡在 listening——畫面一直說「我在聽」、秒數照跳，
  /// 但沒有任何人開始下一輪。長輩看到的就是「講什麼都沒反應」。實機大約撐四輪。
  int _consecutiveListenErrors = 0;

  /// 連續失敗幾次就收手。無限重試會變成一直閃提示的空轉，比直接停下來更難懂。
  static const _maxConsecutiveListenErrors = 3;

  /// 這一輪聆聽失敗了：決定要重開一輪，還是收手回待機。
  void _onListenFailed() {
    _consecutiveListenErrors++;

    if (!_conversationActive) {
      setState(() => _setPhase(_Phase.idle));
      return;
    }

    if (_consecutiveListenErrors >= _maxConsecutiveListenErrors) {
      setState(() {
        _conversationActive = false;
        _setPhase(_Phase.idle);
      });
      // 講清楚下一步要做什麼。停在這裡而不說話的話，長輩只會一直對著手機講。
      _appendHint('現在聽不太到，請按一下麥克風再說一次，或用下方打字。');
      return;
    }

    // 重開一輪之前先讓語音服務收乾淨：Android 的辨識器還沒結束時再 listen，
    // 下一次會立刻再報一次忙碌，變成錯誤接錯誤。
    Future.delayed(const Duration(milliseconds: 400), () async {
      if (!mounted || !_conversationActive) return;
      try {
        await _speech.stop();
      } catch (_) {
        // 已經停了或平台不支援，照樣往下開新的一輪
      }
      if (!mounted || !_conversationActive) return;
      _listenTurn();
    });
  }

  /// 統一切換階段：管理 listening 秒數計時器。
  /// 狀態變化的螢幕報讀由狀態文字的 liveRegion 承載（§9）。
  void _setPhase(_Phase p) {
    if (p == _Phase.listening) {
      _listenSeconds = 0;
      _listenTimer?.cancel();
      _listenTimer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() => _listenSeconds++);
      });
    } else {
      _listenTimer?.cancel();
      _listenTimer = null;
    }
    _phase = p;
  }

  // ---- 免手持語音迴圈 ----

  void _startConversation() {
    setState(() => _conversationActive = true);
    _listenTurn();
  }

  Future<void> _stopConversation() async {
    // 先作廢在飛的那一輪，再停裝置：順序反過來的話，回應可能在 stop 與 setState
    // 之間回來，照樣把階段拉回 speaking。
    _turnSeq++;
    setState(() => _conversationActive = false);
    // 主動停止會讓語音服務回報 notListening，那是預期中的收工，不該被
    // [_onSpeechStatus] 當成「悄悄收工」而補一則「沒聽清楚」。
    _awaitingFinal = false;
    _silenceTimer?.cancel();
    await _speech.stop();
    // 錄到一半按停止：整段丟掉而不是送出。長輩按停止的意思是「不要了」，
    // 把半句話送去辨識並記進資料，跟他的意圖相反。
    await _recorder.cancel();
    await _audio.stop();
    if (mounted) setState(() => _setPhase(_Phase.idle));
  }

  /// 聆聽一句話；靜音斷句後拿到最終文字（華語）或音檔（客語）就送出。
  Future<void> _listenTurn() async {
    if (!_conversationActive || !_micAvailable) return;
    // 只清「正在辨識中」的暫存，不動 _messages——歷史要留著。
    _silenceTimer?.cancel();
    _bestHeard = '';
    setState(() {
      _setPhase(_Phase.listening);
      _question = '';
    });

    if (AppSession.instance.isHakka) return _recordTurn();

    var handled = false; // 每輪只處理一次最終結果

    // 從這裡到收到最終結果之間，若語音服務悄悄收工，要靠 [_onSpeechStatus] 接住。
    _awaitingFinal = true;

    await _speech.listen(
      // 靜音多久算講完。預設 3 秒對長輩太短——他們講一句話中間本來就會停頓
      // （想詞、換氣），一停超過門檻辨識器就結束這一段、重開新的一段，而
      // `recognizedWords` 從新那段的開頭算起，畫面上前半句就整段消失了。
      //
      // 曾經試過在 App 這邊跨段累積，判準是「新的一段不以舊的一段開頭就收起舊的」。
      // 那是錯的：辨識器會**修正已經吐出來的內容**（補標點、改詞），修正後的字串
      // 不見得以前一版開頭，於是舊的被當成獨立一段收起來、新的又是完整句子，
      // 逐字稿變成「把我說的話改成客語把我說的話改成客語四海腔」——而那串會原樣
      // 送到後端當成長輩說的話。與其猜辨識器的行為，不如把門檻放寬讓它別斷。
      pauseFor: const Duration(seconds: 6),
      onResult: (text, isFinal) {
        if (!mounted) return;

        // 顯示的與送出的都走同一份，畫面上看到什麼就是送出什麼。
        final heard = mergeRecognized(_bestHeard, text);
        _bestHeard = heard;
        setState(() => _question = heard);
        // 逐字稿邊講邊長，也要跟著捲，否則長輩看不到自己正在說的那一句。
        // 不用動畫：部分結果來得很密，每次都播動畫畫面會一直抖。
        _scrollToBottom(animate: false);

        // 有文字了才開始算靜音。還沒開口就算的話，會在長輩想詞的時候切掉他。
        if (!isFinal && heard.isNotEmpty) _armSilenceCutoff();

        if (isFinal && !handled) {
          handled = true;
          _silenceTimer?.cancel();
          // 這一輪有結果了，狀態回報不必再補救。
          _awaitingFinal = false;
          // 收到一句完整的就代表辨識器恢復正常了，錯誤計數歸零。
          _consecutiveListenErrors = 0;
          final q = heard.trim();
          if (q.isEmpty) {
            if (_conversationActive) _listenTurn();
          } else {
            _handleQuestion(text: q, continueLoop: true);
          }
        }
      },
    );
  }

  /// 客語：錄音送後端辨識。
  ///
  /// 與華語那條的差別不只是資料形態——**畫面上不會有逐字稿**。裝置端 ASR 邊聽邊給
  /// 文字，錄音沒有；長輩說了什麼要等 `ChatReply.transcript` 回來才知道。所以聆聽
  /// 期間泡泡區是空的，只有狀態文字與秒數在動（那個本來就有）。
  Future<void> _recordTurn() async {
    final started = await _recorder.start(onDone: _onRecordingDone);
    if (!started && mounted) {
      // 沒有麥克風權限。講清楚並停掉迴圈，不然它會一輪一輪空轉。
      setState(() => _conversationActive = false);
      _appendHint('需要麥克風才能聽您說話，請到手機設定開啟。');
      setState(() => _setPhase(_Phase.idle));
    }
  }

  /// 錄音自己停了（講完、沒開口、或到 60 秒上限）。
  Future<void> _onRecordingDone(AudioRecorderStopReason reason) async {
    final audio = await _recorder.stop();
    if (!mounted) return;

    // 一直沒開口：要講出來，否則長輩只看到「我在聽」忽然變回待機，不知道發生什麼事。
    if (reason == AudioRecorderStopReason.noSpeech || audio == null) {
      _appendHint(_notHeardHint);
      if (_conversationActive) {
        _listenTurn();
      } else {
        setState(() => _setPhase(_Phase.idle));
      }
      return;
    }

    await _handleQuestion(audioBase64: audio, continueLoop: true);
  }

  /// 送這一輪到後端，顯示答案並唸出來；[continueLoop] 為 true 且迴圈開啟時唸完自動再聆聽。
  ///
  /// [text] 與 [audioBase64] 擇一：華語送裝置端辨識好的文字，客語送音檔。
  Future<void> _handleQuestion({
    String? text,
    String? audioBase64,
    required bool continueLoop,
  }) async {
    final seq = ++_turnSeq;
    await _speech.stop();
    // 問題定案，移進歷史；暫存的辨識文字清掉，避免同一句出現兩次。
    //
    // 音檔那條還不知道長輩說了什麼——泡泡先留白（`_pendingElderBubble`），等
    // transcript 回來再補。先放一顆空泡泡而不是什麼都不放，是為了讓長輩看得到
    // 「我剛才那句進去了」，畫面不會在思考期間完全空著。
    final bubbleIndex = _messages.length;
    setState(() {
      _setPhase(_Phase.thinking);
      _messages.add(_Message(isElder: true, text: text ?? _pendingElderBubble));
      _question = '';
    });
    _scrollToBottom();

    try {
      final reply = await CareRepo.instance.chat(
        elderId: AppSession.instance.selectedElderId ?? '',
        lang: AppSession.instance.isHakka ? 'hak' : 'zh-TW',
        text: text,
        audioBase64: audioBase64,
      );

      // 音檔那條的逐字稿由後端 ASR 給，回來才補上長輩那顆泡泡。
      if (text == null &&
          mounted &&
          seq == _turnSeq &&
          reply.transcript.trim().isNotEmpty &&
          bubbleIndex < _messages.length) {
        setState(() => _messages[bubbleIndex] =
            _Message(isElder: true, text: reply.transcript));
      }

      // 對話可能已經改了後端的狀態。兩件事都不等回應、也不擋這一輪對話——它們只是
      // 背景把今日畫面、本地通知與語言設定換成新的，對話本身不該為它們停下來。
      //
      // 放在 seq 檢查之前是刻意的：這些副作用**已經發生在後端了**，使用者中途按停止
      // 不會讓它回復。不同步的話，行程明明完成了、語言明明改過了，今日畫面卻還是舊的。
      //
      // 行程不看 `routinesUpdated` 而是每輪都拉：那個旗標只涵蓋後端「知道自己改了」
      // 的情況，而長輩也可能透過別的路徑（照護者同時在改、上一輪漏掉的旗標）讓資料
      // 變動。每輪多一次查詢換掉「畫面一直是舊的」，這個交換划算。
      unawaited(RoutineSync.refresh());
      // 長輩可以用講的改語言與腔調（後端 update_elder_profile），那條路不經過
      // App 的按鈕，不重讀的話語言鈕會跟他實際在用的語言對不上。
      //
      // **要記住這個 future 並在開下一輪之前等它**：長輩說「跟我講客話」之後，
      // 下一輪就該切到錄音那條路。如果只是 unawaited 丟著，`_listenTurn()` 會在
      // 資料回來之前就用舊的 `isHakka` 決定走哪條——他改完語言，下一輪照樣是
      // 華語逐字稿，看起來像根本沒改成功。
      //
      // 實務上不會拖慢：它跟後面的 TTS 播放同時進行，唸完早就回來了。
      _profileRefresh = AppSession.instance.refreshSelectedElder();

      // 使用者在等待期間按了停止（或又開了新的一輪）：這份回應已經沒人要了。
      // 不顯示、不唸、不改階段——那一輪的停止動作已經把畫面收成 idle。
      if (!mounted || seq != _turnSeq) return;
      setState(() {
        _messages.add(_Message(isElder: false, text: reply.replyText));
        _setPhase(_Phase.speaking);
      });
      _scrollToBottom();
      await _speakReply(reply);
    } on ApiException catch (_) {
      if (!mounted || seq != _turnSeq) return;
      setState(() => _conversationActive = false); // 出錯就停迴圈，避免一直重打
      _appendNotHeardHint();
    }

    // 開下一輪之前先讓長者檔案的重讀落地：`_listenTurn` 要靠 `isHakka` 決定走
    // 裝置端辨識還是錄音，用到舊值的話「用講的改語言」會晚一輪才生效。
    // 失敗不擋——拿不到新資料就沿用現在這份，總比停在這裡好。
    try {
      await _profileRefresh;
    } catch (_) {
      // refreshSelectedElder 自己已經吞掉錯誤，這裡只是保險
    }
    _profileRefresh = null;

    if (!mounted || seq != _turnSeq) return;
    setState(() => _setPhase(_Phase.idle));
    if (continueLoop && _conversationActive) _listenTurn();
  }

  /// 唸出回覆：優先播後端合成的音檔，沒有或播不出來就退回裝置端 TTS。
  ///
  /// 後端那把聲音才是長輩該聽到的（客語裝置端 TTS 根本唸不出來），但 presigned URL
  /// 有時效、也可能載不動。這種時候寧可用裝置端唸出同一段文字，也不要讓長輩對著
  /// 一個安靜的畫面等——免手持迴圈是靠「唸完自動再聆聽」串起來的，沒有聲音等於斷掉。
  Future<void> _speakReply(ChatReply reply) async {
    if (reply.replyAudioUrl.isNotEmpty) {
      try {
        await _audio.playUrl(reply.replyAudioUrl);
        return;
      } catch (_) {
        // 落到下面的裝置端 TTS
      }
    }
    await _audio.speak(reply.replyText);
  }

  // ---- 打字備援 ----

  Future<void> _submitText(String raw) async {
    final q = raw.trim();
    if (q.isEmpty || _phase != _Phase.idle) return;
    await _handleQuestion(text: q, continueLoop: false);
  }

  void _openTextInput() {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.cardAlt,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: AppRadius.voicePanel,
      ),
      builder: (_) => _TextInputSheet(onSubmit: _submitText),
    );
  }

  // ---- 狀態文字 ----

  String _statusText(_Phase p) => switch (p) {
        _Phase.listening => t('我在聽，說完停一下'),
        _Phase.thinking => t('聽到了，正在想…'),
        _Phase.speaking => t('我正在說'),
        _Phase.idle => _conversationActive ? t('準備中…') : t('按一下就可以說話'),
      };

  // ---- UI ----

  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.of(context).disableAnimations;
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(context),
            Expanded(child: _buildConversation(context)),
            _buildVoicePanel(context, reduceMotion),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
          // 標題講「這是什麼」，不是問候語——問候語每次進來都一樣，放在最上面
          // 等於用最大的字重複一句沒有資訊的話。時段問候移到下方開場那一句
          // （見 [_ConversationHint]），講完就被對話取代。
          // 不放語言切換（§5.1）；不放日期（今日頁的農民曆牌面才是日期的來源）。
          //
          // 用「聊天夥伴」而不是「聊天室」或「小助手」：「聊天室」是 BBS／MSN 年代的
          // 空間比喻，長輩熟的是「聊天」不是「聊天室」；「助手」則把定位偏成工具，
          // 讓人覺得要有事情才能找它——但這個畫面最重要的情境正是「沒什麼事，
          // 就想講講話」。「夥伴」是日常詞，也守住陪伴而非任務的定位。
          child: Text(t1('{}的聊天夥伴', AppSession.instance.displayName),
              style: text.headlineLarge),
        ),
        // 橫線把標題與對話區切開，讓下方看得出來是另一段內容。
        const Divider(height: 1, thickness: 1.5, color: AppColors.borderDashed),
      ],
    );
  }

  /// 加一則「沒聽清楚」提示。連續失敗時不重複加，否則長輩會被同一句洗版。
  void _appendNotHeardHint() => _appendHint(_notHeardHint);

  /// 加一則 AI 側的提示訊息；與上一則相同就不重複加。
  ///
  /// [zh] 傳華語原文，這裡才換成長輩選的書寫語言。**提示訊息是 App 自己講的話，
  /// 跟泡泡裡後端回的內容不同**——後者一律原樣顯示，不經過 [t]。
  void _appendHint(String zh) {
    final message = t(zh);
    if (_messages.isNotEmpty && _messages.last.text == message) return;
    setState(() => _messages.add(_Message(isElder: false, text: message)));
    _scrollToBottom();
  }

  /// 捲到最新的一句。等這一幀畫完才捲，否則 maxScrollExtent 還是舊的。
  ///
  /// 捲兩次是必要的：泡泡裡是會換行的長文字，第一幀之後高度可能還在長
  /// （字體、換行、圖片都會影響），只捲一次會停在半途，最新那句露不出來。
  /// 第二次補在 120ms 後，涵蓋掉那段成長。
  ///
  /// [animate] 為 false 時直接跳——聆聽中的逐字稿每來一個部分結果就捲一次，
  /// 每次都播動畫會讓畫面一直抖。
  void _scrollToBottom({bool animate = true}) {
    void go() {
      if (!mounted || !_scrollCtrl.hasClients) return;
      final target = _scrollCtrl.position.maxScrollExtent;
      if ((target - _scrollCtrl.offset).abs() < 4) return;
      if (animate) {
        _scrollCtrl.animateTo(
          target,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      } else {
        _scrollCtrl.jumpTo(target);
      }
    }

    WidgetsBinding.instance.addPostFrameCallback((_) {
      go();
      Future.delayed(const Duration(milliseconds: 120), go);
    });
  }

  Widget _buildConversation(BuildContext context) {
    final text = Theme.of(context).textTheme;

    // 還沒講過話時這塊是空的——一整片留白會讓長輩不確定自己是不是按錯了。
    // 放範例句而不是插圖：它同時回答「這裡能做什麼」和「我該說什麼」。
    if (_messages.isEmpty && _question.isEmpty) {
      return _ConversationHint(
        greeting: '${AppSession.instance.displayName}，${_greeting()}！',
      );
    }

    // 最後一項是正在辨識中、還沒定案的那句（如果有）。
    final pendingCount = _question.isNotEmpty ? 1 : 0;
    return ListView.builder(
      controller: _scrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      itemCount: _messages.length + pendingCount,
      itemBuilder: (context, i) {
        final isPending = i >= _messages.length;
        final isElder = isPending || _messages[i].isElder;
        return _Bubble(
          isElder: isElder,
          child: Text(
            isPending ? _question : _messages[i].text,
            style: isElder
                ? text.headlineSmall?.copyWith(color: AppColors.onDark)
                : text.headlineSmall,
          ),
        );
      },
    );
  }

  Widget _buildVoicePanel(BuildContext context, bool reduceMotion) {
    final text = Theme.of(context).textTheme;
    return Container(
      width: double.infinity,
      decoration: const BoxDecoration(
        color: AppColors.cardAlt,
        borderRadius: AppRadius.voicePanel,
        boxShadow: AppShadows.voicePanel,
      ),
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (!_micAvailable)
            Text(t('這台裝置沒有麥克風，請用下方打字'),
                textAlign: TextAlign.center,
                style:
                    text.headlineSmall?.copyWith(color: AppColors.inkSecondary))
          else ...[
            _MicOrb(
              phase: _phase,
              pulse: _pulse,
              reduceMotion: reduceMotion,
              // thinking 期間不給按：這時上一句已經送出、還在等回覆，按下去
              // 會開始新的一輪聆聽，把還在飛的那一輪晾在那裡。
              //
              // 判斷要看 [_phase] 而不是 [_conversationActive]——打字送出時
              // continueLoop 是 false，迴圈沒開但階段照樣是 thinking，只看
              // _conversationActive 會落到 _startConversation 去。
              onTap: _phase == _Phase.thinking
                  ? null
                  : (_conversationActive
                      ? _stopConversation
                      : _startConversation),
            ),
            const SizedBox(height: 12),
            // liveRegion：狀態文字變化時由螢幕報讀器朗讀（§9）。
            Semantics(
              liveRegion: true,
              child: Text(
                _phase == _Phase.listening
                    ? t2('{}（{} 秒）', _statusText(_phase), _listenSeconds)
                    : _statusText(_phase),
                textAlign: TextAlign.center,
                style: text.headlineMedium,
              ),
            ),
          ],
          const SizedBox(height: 16),
          // 次要方式：常駐可見的打字按鈕（非隱藏 toggle，§5.1）。
          SizedBox(
            height: 60,
            child: OutlinedButton.icon(
              onPressed: _phase == _Phase.idle ? _openTextInput : null,
              icon: const Icon(Icons.keyboard, size: 28),
              label: Text(t('改用打字'), style: text.headlineSmall),
              style: OutlinedButton.styleFrom(
                foregroundColor: AppColors.ink,
                side: const BorderSide(color: AppColors.border, width: 2),
                shape: const RoundedRectangleBorder(
                  borderRadius: BorderRadius.all(AppRadius.field),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 開場問候。分界與今日頁的早安圖共用一份（見 [GreetingSlot]）——
  /// 同一個時間點，撕曆上寫「晚安」而聊天室說「早安」是不能發生的。
  String _greeting() => GreetingSlot.of(DateTime.now()).text;
}

/// 對話中的一則訊息。
class _Message {
  const _Message({required this.isElder, required this.text});

  final bool isElder;
  final String text;
}

/// 開始對話前的引導：時段問候 + 健康資訊免責聲明，整個對話區正中央。純顯示，
/// 不可互動——長者模式的三個互動額度要留給麥克風、打字、底部分頁。
///
/// 原本這裡還有「你可以這樣說」跟三句範例，但那三句是靠左的卡片，
/// 看起來像「可以點的選項」卻不能點；拿掉之後聊天室一開始就只留一句問候，
/// 乾淨地待在畫面正中間，不會有東西看起來像按鈕卻按不下去。
///
/// 免責聲明放在這裡而不是底部語音面板：那塊已經有麥克風、狀態文字與打字按鈕，
/// 再加一行會壓縮對話區的高度；開場這片留白本來就是空的，擺進來不佔任何版面成本，
/// 而且每次進聊天室都會先看到。完整說明在註冊時的 [ConsentPolicyScreen]。
class _ConversationHint extends StatelessWidget {
  const _ConversationHint({required this.greeting});

  /// 例：「阿蘭嬤，早安！」——依時段變化，講完就被對話取代。
  final String greeting;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    // 捲動容器：兩倍字級下問候加聲明會比這塊空間高，硬塞會 overflow。
    // 包 SingleChildScrollView 之後內容照樣置中（靠 minHeight 撐滿可用高度），
    // 只有真的放不下時才變成可捲。
    return LayoutBuilder(
      builder: (context, constraints) => SingleChildScrollView(
        child: ConstrainedBox(
          constraints: BoxConstraints(minHeight: constraints.maxHeight),
          child: Padding(
            padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.lg, vertical: AppSpacing.lg),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                _HintLine(text: greeting, style: text.headlineLarge),
                const SizedBox(height: AppSpacing.xl),
                // 講「看醫生」而不是「非醫療診斷之參酌」：長輩要看得懂才有意義，
                // 法律用語留在政策頁。字級仍守長者下限 24sp。
                Text(
                  t('我說的健康資訊只能參考，\n身體不舒服要看醫生喔'),
                  textAlign: TextAlign.center,
                  style: text.headlineSmall?.copyWith(
                    fontWeight: FontWeight.w500,
                    color: AppColors.inkSecondary,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 引導的一行：朱紅星芒 + 置中的字。
///
/// 星芒用畫的不用字元——`✳` 這類符號不在 Noto Serif/Sans TC 的字集裡，
/// 打包字體後會變成缺字方框。
class _HintLine extends StatelessWidget {
  const _HintLine({required this.text, required this.style});

  final String text;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) {
    final size = (style?.fontSize ?? 24) * 0.8;
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          // 對齊第一行文字的視覺中線，而不是整段文字的頂端
          padding: EdgeInsets.only(top: size * 0.35),
          child: CustomPaint(
            size: Size.square(size),
            painter: const _AsteriskPainter(),
          ),
        ),
        SizedBox(width: size * 0.45),
        Flexible(
          child: Text(text, textAlign: TextAlign.center, style: style),
        ),
      ],
    );
  }
}

/// 八芒星，朱紅。手繪而非圖示字型：只有八條從中心放射的線，
/// 換字體、換平台都長一樣。
class _AsteriskPainter extends CustomPainter {
  const _AsteriskPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.width / 2;
    final paint = Paint()
      ..color = AppColors.accentText
      ..strokeWidth = size.width * 0.12
      ..strokeCap = StrokeCap.round;

    for (var i = 0; i < 8; i++) {
      final angle = i * math.pi / 4;
      // 斜的四條短一點，八芒星才有長短交錯的節奏，不會看起來像一團毛球
      final len = radius * (i.isEven ? 1.0 : 0.72);
      canvas.drawLine(
        center,
        center + Offset(math.cos(angle) * len, math.sin(angle) * len),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _AsteriskPainter oldDelegate) => false;
}

/// 四狀態麥克風。每態外形／內容不同（§5.3），不只靠顏色。
class _MicOrb extends StatelessWidget {
  const _MicOrb({
    required this.phase,
    required this.pulse,
    required this.reduceMotion,
    required this.onTap,
  });

  final _Phase phase;
  final AnimationController pulse;
  final bool reduceMotion;

  /// null 代表此刻不可按（thinking 期間）。Semantics 的 `button` 會跟著關掉，
  /// 螢幕報讀器才不會把一顆按不動的東西讀成按鈕。
  final VoidCallback? onTap;

  /// 圓球直徑。仍遠大於 60dp 觸控下限，縮小是為了把畫面留給對話內容。
  static const double _size = 84;

  @override
  Widget build(BuildContext context) {
    final pulsing = !reduceMotion &&
        (phase == _Phase.listening || phase == _Phase.speaking);
    return Semantics(
      button: onTap != null,
      enabled: onTap != null,
      label: switch (phase) {
        _Phase.idle => t('開始說話'),
        _Phase.listening => t('聆聽中，點一下結束'),
        _Phase.thinking => t('思考中，請稍等'),
        _Phase.speaking => t('回覆中，點一下停止'),
      },
      child: GestureDetector(
        onTap: onTap,
        child: SizedBox(
          // 比圓球大一圈，容納脈動外環的最大半徑。
          width: 124,
          height: 124,
          child: Center(
            child: Stack(
              alignment: Alignment.center,
              children: [
                if (pulsing)
                  AnimatedBuilder(
                    animation: pulse,
                    builder: (_, __) => Container(
                      width: _size + 38 * pulse.value,
                      height: _size + 38 * pulse.value,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: AppColors.accent
                            .withValues(alpha: 0.35 * (1 - pulse.value)),
                      ),
                    ),
                  ),
                _core(),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _core() {
    // thinking：空心紙底 + 5dp 深色外框 + 朱紅小章；其餘：實心 accent。
    if (phase == _Phase.thinking) {
      return Container(
        width: _size,
        height: _size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: AppColors.app,
          border: Border.all(color: AppColors.barDark, width: 5),
        ),
        alignment: Alignment.center,
        child: Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: AppColors.accentText,
            borderRadius: BorderRadius.circular(10),
          ),
          alignment: Alignment.center,
          child: Text(t('想'),
              style: const TextStyle(
                  color: Colors.white,
                  fontSize: 24,
                  fontWeight: FontWeight.w900)),
        ),
      );
    }
    final icon = switch (phase) {
      _Phase.speaking => Icons.graphic_eq,
      _Phase.listening => Icons.mic,
      _ => Icons.mic_none,
    };
    return Container(
      width: _size,
      height: _size,
      decoration: const BoxDecoration(
        shape: BoxShape.circle,
        color: AppColors.accent,
        boxShadow: AppShadows.mic,
      ),
      // listening/speaking 加一圈深色外環，靜態也可辨（§5.3 適配）。
      foregroundDecoration: phase == _Phase.idle
          ? null
          : BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(color: AppColors.accentPressed, width: 5),
            ),
      alignment: Alignment.center,
      child: Icon(icon, size: 42, color: Colors.white),
    );
  }
}

/// 對話泡泡。isElder=true 走深色右對齊、false 走 AI 卡片左對齊。
class _Bubble extends StatelessWidget {
  const _Bubble({required this.isElder, required this.child});

  final bool isElder;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final bubble = Container(
      constraints:
          BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.82),
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
      decoration: BoxDecoration(
        color: isElder ? AppColors.barDark : AppColors.card,
        borderRadius: isElder ? AppRadius.bubbleElder : AppRadius.bubbleAi,
        boxShadow: isElder ? null : AppShadows.bubble,
      ),
      child: child,
    );
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment:
            isElder ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (!isElder) ...[
            Container(
              width: 40,
              height: 40,
              margin: const EdgeInsets.only(top: 2, right: 8),
              decoration: const BoxDecoration(
                color: AppColors.avatarBg,
                shape: BoxShape.circle,
              ),
              alignment: Alignment.center,
              child: const Icon(Icons.spa, size: 22, color: AppColors.avatarFg),
            ),
          ],
          Flexible(child: bubble),
        ],
      ),
    );
  }
}

/// 打字備援輸入面板（bottom sheet）。長者字級 >=24sp、有明確關閉鈕（§6 modal escape）。
class _TextInputSheet extends StatefulWidget {
  const _TextInputSheet({required this.onSubmit});

  final Future<void> Function(String) onSubmit;

  @override
  State<_TextInputSheet> createState() => _TextInputSheetState();
}

class _TextInputSheetState extends State<_TextInputSheet> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + bottomInset),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(t('打字給我'), style: text.headlineSmall),
              IconButton(
                onPressed: () => Navigator.of(context).pop(),
                iconSize: 32,
                tooltip: t('關閉'),
                icon: const Icon(Icons.close, color: AppColors.ink),
              ),
            ],
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _controller,
            autofocus: true,
            minLines: 1,
            maxLines: 4,
            style: text.headlineSmall,
            decoration: InputDecoration(
              hintText: t('想問什麼都可以'),
              hintStyle: const TextStyle(color: AppColors.hint),
              filled: true,
              fillColor: AppColors.cardAlt,
              // 不畫框，與其他輸入欄一致；框只在聚焦時出現。
              enabledBorder: InputBorder.none,
              focusedBorder: const OutlineInputBorder(
                borderRadius: BorderRadius.all(AppRadius.field),
                borderSide: BorderSide(color: AppColors.accent, width: 2),
              ),
            ),
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 60,
            child: FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.accentText,
                foregroundColor: Colors.white,
                shape: const RoundedRectangleBorder(
                  borderRadius: BorderRadius.all(AppRadius.field),
                ),
              ),
              onPressed: () {
                final q = _controller.text;
                Navigator.of(context).pop();
                widget.onSubmit(q);
              },
              child: Text(t('送出'), style: text.headlineSmall),
            ),
          ),
        ],
      ),
    );
  }
}
