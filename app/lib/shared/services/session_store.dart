import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/caregiver.dart';
import '../models/elder.dart';
import 'api_error_codes.dart';
import 'api_exception.dart';
import 'care_repository.dart';

/// App 執行期的長者情境（Demo 用）。
///
/// 兩部分：
/// - **首次設定的本機暫存**（[elderName]／[elderNickname]／[lang]）：登入與 `POST /elders`
///   尚未接上，S1 先把資料寫在這裡供問候語與輸入路徑使用。
/// - **長者清單與目前選定的長者**（[elders]／[selectedElderId]）：對齊 api.md——一位照護者
///   可綁定多位長者（`elders.caregiver_ids`），而 `/summaries`、`/events`、`/routines`、
///   `/stats` 每一個都要求 `elder_id`，所以「目前在看誰」必須是全域狀態。
///   UI 上先只在管理頁切換，但資料結構一開始就照多位設計，之後不用重寫。
///
/// 另用 shared_preferences 持久化「是否已完成首次設定」、長者基本資料與上次選定的長者。
class AppSession {
  AppSession._();
  static final AppSession instance = AppSession._();

  /// 「已完成首次設定」的 key，綁 Cognito `sub`。
  ///
  /// 為什麼要按帳號存而不是一台裝置一個旗標：這件事是**帳號**的屬性，不是裝置的。
  /// 裝置層級的旗標會在兩種情況下害人跳過建立資料——照護者在這台瀏覽器用過 `/setup`
  /// 之後長者登入（旗標已是 true），以及一位長者登出、換另一位長者登入。
  /// 兩者都會直接進今日頁，而系統裡根本沒有這位長者的資料。
  static String _setupDoneKey(String accountId) => 'setup_done_$accountId';

  // 舊的裝置層級 key `setup_done` **刻意不遷移、也不再讀**：那個 true 是誰按出來的
  // 已經無從得知（很可能是登入功能還不存在時、照護者自己填的），無法歸屬到任何帳號。
  // 猜錯的代價是讓長者跳過建立基本資料——之後每一頁都沒有稱呼與行程可用；
  // 重填一次的成本低得多，所以直接忽略，讓它留在那裡不再有任何效力。

  /// 註冊流程中填好、但還沒有帳號可以掛的長輩資料，key 前綴（後面接 email）。
  ///
  /// **為什麼要按 email 暫存，不直接寫進帳號**：建立基本資料被安排在註冊流程之內
  /// （註冊 → /setup → 驗證碼 → 登入），而那個時間點使用者**還沒登入**，拿不到
  /// Cognito `sub`；而「已完成設定」這件事是**帳號**的屬性（[_setupDoneKey] 綁 sub），
  /// 沒有 sub 就沒有正確的地方可以寫。硬寫成裝置層級的旗標正是先前修掉的 bug——
  /// 同一台裝置換人登入會被誤判成已設定，長者就此跳過建資料。
  ///
  /// email 是註冊那一刻唯一能識別帳號的東西，所以先寄放在 email 底下，第一次登入拿到
  /// sub 之後由 [consumePendingSetup] 兌現到該帳號並清掉暫存。做法與
  /// `AuthService` 的 `auth_pending_role_` 一致（身分也是同一個時序問題）。
  static const _kPendingSetupPrefix = 'setup_pending_';

  // 全部綁 sub，與 [_setupDoneKey] 一致。
  //
  // 原本這幾個是裝置層級的全域 key，於是登出時只能連同「已完成設定」的旗標一起刪掉
  // ——否則下一個登入的人會看到上一位長輩的稱呼。代價是**同一個人登出再登入也要
  // 重走一次首次設定**，那是使用者實際踩到的問題。
  //
  // 綁 sub 之後兩件事同時成立：不同帳號天然隔離（各讀各的 key），同一個帳號重登
  // 資料還在。登出因此不需要刪任何持久化資料，只要把記憶體欄位歸零。
  static String _nameKey(String a) => 'elder_name_$a';
  static String _nicknameKey(String a) => 'elder_nickname_$a';
  static String _birthYearKey(String a) => 'elder_birth_year_$a';
  static String _addressRegionKey(String a) => 'elder_address_region_$a';
  static String _dialectKey(String a) => 'elder_hakka_dialect_$a';
  static String _langKey(String a) => 'elder_lang_$a';
  static String _textLangKey(String a) => 'elder_text_lang_$a';
  static String _selectedElderKey(String a) => 'selected_elder_id_$a';

  String elderName = '';
  String elderNickname = '';

  /// 出生年（西元）。對齊 api.md 的 `birth_year`；沒填過為 null。
  ///
  /// 存年份而不是年齡：年齡每年會變，存下來隔年就是錯的。畫面要顯示歲數時由
  /// 當年減出生年算（管理頁就是這樣做）。
  int? elderBirthYear;

  /// 客語腔調（api.md 的 hakka_dialect）。只在 lang 是 hak 時有意義。
  ///
  /// **這個值一定要進得了長者檔案才算數**：後端只讀 elder profile 的腔調，
  /// /chat 不帶它（api.md）。存在這裡是初次設定到 POST /elders 之間的過渡。
  String elderHakkaDialect = HakkaDialect.defaultValue;

  /// 居住地區，如「台北市大安區」。對齊 api.md 的 `address_region`。
  String elderAddressRegion = '';

  /// 語言偏好，對齊 api.md：'zh-TW' | 'hak'。決定長者端輸入路徑。
  String lang = 'zh-TW';

  /// [lang] 是不是這個帳號**自己選過**的（首次設定填的，或長者按過語言鈕）。
  ///
  /// 需要跟「沒選過」分得出來，是因為 [lang] 沒有值時預設 `'zh-TW'`——那跟長者
  /// 明確選了華語長得一模一樣。[isHakka] 在沒選過時要退回 [selectedElder]，
  /// 選過就不能再退（否則長者選華語、照護者那邊是客語時，永遠切不回華語）。
  bool _langChosen = false;

  /// 語言變動的廣播，用法同 [RoutineSync.revision]。
  ///
  /// 需要它的理由也一樣：長者模式兩個 tab 掛在 `StatefulNavigationShell` 底下，
  /// 切走再切回來 State 是留著的、`initState` 不會重跑。而聊天頁開場問的權限
  /// 兩種語言並不同（華語問裝置端辨識、客語問錄音），不重問就會一直拿著
  /// 切換前那個答案。
  static final ValueNotifier<int> langRevision = ValueNotifier<int>(0);

  /// 畫面文字的書寫語言（`'zh-TW'` ｜ `'hak'`）。**跟 [lang] 是兩件事。**
  ///
  /// 講客語的長輩不一定讀得懂客語漢字——有人講客語但只認得一般漢字。兩者共用一個
  /// 值等於逼這種人在「聽不懂語音」和「看不懂畫面」之間二選一，所以各存各的。
  ///
  /// 純本機、不對應 api.md 任何欄位：`lang_preference` 講的是語音，後端沒有「畫面
  /// 用哪種字」這個概念，也不需要知道。
  String textLang = 'zh-TW';

  /// 畫面文字是不是客語漢字。沒有 [lang] 那種退回照護者設定的問題——這個值只有
  /// 長輩自己設得了，沒設就是華語。
  bool get isHakkaText => textLang == 'hak';

  /// 畫面文字語言變動的廣播。長者端各畫面監聽它重畫。
  static final ValueNotifier<int> textLangRevision = ValueNotifier<int>(0);

  /// 長者自己切換畫面文字的書寫語言。
  Future<void> setTextLang(String lang) async {
    if (textLang == lang) return;
    textLang = lang;
    final account = _accountId;
    if (account != null) {
      final p = await SharedPreferences.getInstance();
      await p.setString(_textLangKey(account), lang);
    }
    textLangRevision.value++;
  }

  /// 長者自己切換語音語言（`'zh-TW'` ｜ `'hak'`），立刻生效並記到下次啟動。
  ///
  /// **只寫本機、不送後端**：canonical 的 `lang_preference` 是照護者專屬欄位
  /// （`PATCH /elders/{id}`，見 api.md 端點總覽），長者帳號改不動它。所以照護者
  /// 設的值仍在後端，長者這一份蓋在它上面——見 [isHakka] 的優先序，實際在說話的
  /// 人贏，這是刻意的。
  ///
  /// TODO(backend): 若後端開放長者本人 PATCH 自己的 `lang_preference`，這裡要一併
  ///   送上去。在那之前換裝置或重裝會退回照護者設的值。
  Future<void> setLang(String lang) async {
    if (_langChosen && this.lang == lang) return;
    this.lang = lang;
    _langChosen = true;
    final account = _accountId;
    if (account != null) {
      final p = await SharedPreferences.getInstance();
      await p.setString(_langKey(account), lang);
    }
    langRevision.value++;
  }

  /// 從後端重讀長者資料，並把**後端那邊發生的**語言與腔調變更套到畫面上。
  ///
  /// 為什麼需要這條：長輩可以**用講的**改語言與腔調（後端的 `update_elder_profile`
  /// 工具），那條路完全不經過 App 的按鈕。不重讀的話本機永遠停在舊值——今日頁那三顆
  /// 鈕會跟長輩實際在用的語言對不上，而他剛剛才親口改過。
  ///
  /// 後端的值在這裡**優先於**本機按鈕留下的選擇：長輩剛講完的那句話比他上次按鈕新。
  /// 這與 [isHakka] 的精神一致——實際在說話的人贏。
  ///
  /// 失敗一律吞掉：這是背景同步，取不到就維持現狀，不該打斷對話或讓畫面跳錯誤。
  Future<void> refreshSelectedElder() async {
    if (selectedElderId == null) return;
    final before = selectedElder?.langPreference;
    try {
      await loadElders();
    } catch (_) {
      return;
    }
    final after = selectedElder?.langPreference;
    if (after != null && after.isNotEmpty && after != before && after != lang) {
      await setLang(after); // 內含持久化與 langRevision++
    }
    // 腔調鈕讀的是 selectedElder，資料換過就要讓它重畫。
    // 借用 textLangRevision：那三顆鈕都訂閱它（它們在今日頁上是 const，
    // 不訂閱就不會被重建，見 lang_toggle.dart 的說明）。
    textLangRevision.value++;
  }

  /// 是否已完成首次設定；決定登入後的落點（見 app_router 的 redirect）。
  ///
  /// 值屬於 [_accountId] 這個帳號，換帳號後必須重新 [loadForAccount]。
  bool setupDone = false;

  /// 這份狀態屬於哪個帳號（Cognito `sub`）；null = 未登入。
  ///
  /// 單例活得比登入狀態久，所以要記住「現在手上這份資料是誰的」，
  /// 才不會把上一個人的設定寫到下一個人的 key 底下。
  String? _accountId;

  /// 目前登入的照護者自己的身分（`GET /me`）。尚未載入或不是照護者時為 null。
  ///
  /// 屬於 [_accountId] 這個帳號，換帳號後必須重新載入——報錯 ID 的後果是家人綁到別人。
  Caregiver? me;

  /// 照護者可存取的長者（`GET /elders`）。尚未載入時為空。
  List<Elder> elders = const [];

  /// 目前在看哪一位長者；所有照護者畫面的 `elder_id` 都取自這裡。
  String? selectedElderId;

  /// 已連結到這位長者的照護者。長者端「連結家人」頁的資料來源。
  ///
  /// 可以有多位（子女各自一組），所以是清單而非單一值。存的是完整的
  /// [Caregiver] 而不只是 ID——長輩畫面上要看得出「這是誰」，一串 `cg_7f3a91c2`
  /// 對長輩沒有任何意義（api.md 也因此讓綁定 response 帶 `name`）。
  ///
  /// 真實來源是 `GET /elders/{id}/caregivers`，這裡只是快取，見 [ensureCaregiversLoaded]。
  List<Caregiver> linkedCaregivers = const [];

  /// 這台長者手機是否已經有家人連結。決定今天頁要不要顯示連結入口。
  bool get hasLinkedCaregiver => linkedCaregivers.isNotEmpty;

  /// 目前選定的長者物件；清單還沒載入或該 id 已不存在時為 null。
  Elder? get selectedElder {
    final id = selectedElderId;
    if (id == null) return null;
    for (final e in elders) {
      if (e.elderId == id) return e;
    }
    return null;
  }

  /// 客語裝置端 ASR 不支援，走錄音送後端；華語走裝置端辨識。
  ///
  /// 順序與 [displayName] 一致、理由也一樣：[lang] 只有這個帳號自己走過 /setup 時
  /// 才有值，而未接後端時 [selectedElder] 是 demo 假名冊無條件灌進來的（永遠非 null、
  /// 永遠是 `zh-TW`）。原本的順序讓假名冊蓋過本人選的語言——長者選了客語也永遠判成華語。
  ///
  /// 目前還看不出後果：客語分流尚未實作（chat_screen 仍一律走華語迴圈），所以這是
  /// 「等客語接上就會立刻踩到、屆時很難聯想到這裡」的那種問題，先修掉。
  ///
  /// 判斷的是 [_langChosen] 而不是 `lang == 'hak'`：長者按語言鈕選華語時，
  /// 只比對 `'hak'` 會讓它落到 [selectedElder]，而照護者那邊設的若是客語就會把
  /// 長者的選擇蓋掉——鈕按了沒反應。選過就以選的為準，兩個方向都要成立。
  bool get isHakka {
    if (_langChosen) return lang == 'hak';
    return selectedElder?.langPreference == 'hak';
  }

  /// 稱呼 fallback：這個帳號首次設定填的 → 選定長者的暱稱／姓名 → 通用占位。
  ///
  /// 為什麼「自己填的」排在 [selectedElder] 前面：[elderNickname]／[elderName] 只有在
  /// **這個帳號自己走過 /setup** 時才有值（見 loadForAccount），也就是長者本人填的稱呼；
  /// 而未接後端時 [elders] 是 demo 假名冊無條件灌進來的（陳阿蘭／林金水），對長者帳號來說
  /// 那是別人的資料。原本的順序讓假名冊蓋過本人填的名字，長者不管改成什麼、登入後都被叫
  /// 「阿蘭嬤」。照護者帳號不受影響：他們不走 /setup，這兩個欄位是空的，照樣落到
  /// [selectedElder]（切換長輩才會跟著換稱呼）。
  ///
  /// TODO: 後端上線、`GET /elders` 回真名冊之後，長者本人的資料就在 [selectedElder] 裡，
  /// 這兩層的先後就不再有差別，屆時可以收斂回單一來源。
  String get displayName {
    if (elderNickname.trim().isNotEmpty) return elderNickname.trim();
    if (elderName.trim().isNotEmpty) return elderName.trim();
    final e = selectedElder;
    if (e != null) {
      if (e.nickname != null && e.nickname!.trim().isNotEmpty) {
        return e.nickname!.trim();
      }
      if (e.name.trim().isNotEmpty) return e.name.trim();
    }
    return '阿公／阿嬤';
  }

  /// 載入某個帳號已保存的設定狀態與長者資料（首次未設定則維持預設）。
  ///
  /// 呼叫時機有兩個：App 啟動（還原登入狀態之後，見 main.dart）、以及登入成功之後
  /// （見 SignInScreen）。第二個不能省——單例裡可能還放著上一個帳號的值。
  ///
  /// [accountId] 傳 `AuthService.instance.identity?.userId`。未登入（null）時一律視為
  /// **未完成設定**：這時候沒有任何帳號可以歸屬，猜「已完成」會讓長者跳過建立資料。
  ///
  /// TODO(backend): 後端上線後這件事不該由 App 記——改用 `GET /elders`
  ///   （長者只會回自己那一筆）判定：有資料就是設定完成，沒有就進 /setup。
  ///   本機旗標連同 [saveSetup] 的持久化一併移除。
  Future<void> loadForAccount(String? accountId) async {
    _accountId = accountId;
    // 上一個帳號的 ID 不能留：這一頁的用途就是把 ID 報給家人，顯示錯的比不顯示更糟。
    me = null;
    final p = await SharedPreferences.getInstance();
    if (accountId == null) {
      // 未登入：沒有帳號可歸屬，一律回到預設值，不從別的帳號借資料。
      setupDone = false;
      elderName = '';
      elderNickname = '';
      elderBirthYear = null;
      elderAddressRegion = '';
      elderHakkaDialect = HakkaDialect.defaultValue;
      lang = 'zh-TW';
      _langChosen = false;
      textLang = 'zh-TW';
      selectedElderId = null;
      linkedCaregivers = const [];
      return;
    }
    setupDone = p.getBool(_setupDoneKey(accountId)) ?? false;
    elderName = p.getString(_nameKey(accountId)) ?? '';
    elderNickname = p.getString(_nicknameKey(accountId)) ?? '';
    elderBirthYear = p.getInt(_birthYearKey(accountId));
    elderAddressRegion = p.getString(_addressRegionKey(accountId)) ?? '';
    elderHakkaDialect =
        p.getString(_dialectKey(accountId)) ?? HakkaDialect.defaultValue;
    // key 存不存在就是「選過沒有」——寫進去的只可能是 /setup 或語言鈕。
    final savedLang = p.getString(_langKey(accountId));
    _langChosen = savedLang != null;
    lang = savedLang ?? 'zh-TW';
    textLang = p.getString(_textLangKey(accountId)) ?? 'zh-TW';
    selectedElderId = p.getString(_selectedElderKey(accountId));
    // 已連結的家人不從本機讀：那份資料的真實來源是後端，見 [ensureCaregiversLoaded]。
    // 換帳號時一律清空，等畫面自己去載——留著上一個帳號的家人清單是資料外洩。
    linkedCaregivers = const [];
  }

  /// 確保 [me] 已載入並回傳；未登入時回 null（沒有帳號可以問「我是誰」）。
  ///
  /// 只在照護者模式呼叫——`GET /me` 對長者帳號回 403 `FORBIDDEN`。
  Future<Caregiver?> ensureMeLoaded() async {
    final cached = me;
    if (cached != null) return cached;
    // 登入時由 [loadForAccount] 帶進來的 Cognito `sub`。
    // 真後端從 token 認人，sub 只有 demo 那條路用得到（見 CareRepository.me）。
    final sub = _accountId;
    if (sub == null) return null;
    return me = await CareRepo.instance.me(sub: sub);
  }

  /// 連結一位照護者到這位長者。回傳的 `isNew` 為 false 表示早就綁過了。
  ///
  /// 查無此 ID 會丟 [ApiException]（code `CAREGIVER_NOT_FOUND`），由畫面接住——
  /// 那是長輩打錯字時唯一的線索，不能在這裡吞掉變成「連結成功」。
  Future<CaregiverLink> linkCaregiver(String caregiverId) async {
    final elderId = selectedElderId;
    if (elderId == null) {
      throw const ApiException('還不知道要連結到哪位長輩',
          code: ApiErrorCodes.elderNotFound);
    }
    final result = await CareRepo.instance
        .linkCaregiver(elderId: elderId, caregiverId: caregiverId);
    if (result.isNew) {
      linkedCaregivers = [...linkedCaregivers, result.caregiver];
    }
    return result;
  }

  /// 確保已連結的家人清單載入過；已有資料就直接返回。
  ///
  /// **這裡**不做持久化：真實來源是後端（`GET /elders/{id}/caregivers`），在 AppSession
  /// 再存一份只會在「家人用另一台裝置綁定」時對不上。要不要落地是資料來源的事——
  /// 未接後端時由 demo 那份代答，它為了 demo 流程確實有存（見 `DemoRepository`）。
  Future<void> ensureCaregiversLoaded() async {
    if (linkedCaregivers.isNotEmpty) return;
    await ensureEldersLoaded();
    final elderId = selectedElderId;
    if (elderId == null) return;
    linkedCaregivers = await CareRepo.instance.caregivers(elderId: elderId);
  }

  /// 確保長者清單已載入；已有資料就直接返回。
  ///
  /// 四個照護者畫面都需要 `elder_id`，但它們各自獨立載入資料，
  /// 所以由這裡去重，避免切一次 tab 就重打一次 `GET /elders`。
  Future<void> ensureEldersLoaded() async {
    if (elders.isNotEmpty) return;
    await loadElders();
  }

  /// 載入長者清單並確保 [selectedElderId] 有效（沒選過或選的已不在清單時挑第一位）。
  Future<void> loadElders() async {
    elders = await CareRepo.instance.elders();
    if (elders.isEmpty) {
      selectedElderId = null;
      return;
    }
    if (selectedElder == null) await selectElder(elders.first.elderId);
  }

  /// 建立一位長輩（`POST /elders`），加進清單並切換過去。
  ///
  /// 建完就切過去：照護者剛填完這位長輩的資料，下一步一定是幫他設定行程，
  /// 停在上一位身上等於逼人再點一次切換，而且很容易沒注意到就把行程加到別人身上。
  Future<Elder> createElder(Map<String, dynamic> fields) async {
    final created = await CareRepo.instance.createElder(fields);
    elders = [...elders, created];
    await selectElder(created.elderId);
    return created;
  }

  /// 把語言偏好與腔調同步進長者檔案（`PATCH /elders/{id}`）。
  ///
  /// 後端已開放長者本人改這兩個欄位（欄位層級白名單，其餘仍是照護者專屬），
  /// 所以長者端的選擇終於能寫進檔案，而不是只留在這台裝置。
  ///
  /// **失敗不往上拋**：語言的本機值已經生效（`/chat` 每次都帶 `lang`），同步失敗
  /// 的後果只是換裝置會退回舊值，不該讓長輩看到錯誤而以為語言沒切成功。腔調則
  /// 相反——它只讀檔案，同步失敗就是真的沒生效，所以回傳成功與否讓呼叫端決定
  /// 要不要講。
  Future<bool> syncLangFields({String? langPreference, String? dialect}) async {
    final elderId = selectedElderId;
    if (elderId == null) return false;
    try {
      final updated = await CareRepo.instance.updateElder(elderId, {
        if (langPreference != null) 'lang_preference': langPreference,
        if (dialect != null) 'hakka_dialect': dialect,
      });
      replaceElder(updated);
      return true;
    } catch (_) {
      return false;
    }
  }

  /// 就地換掉清單裡的那一筆長者。
  ///
  /// 全 App 的長者資料只有這一份，改完要就地換掉，否則 [selectedElder] 讀到的
  /// 還是舊值——腔調尤其明顯，畫面上的選取狀態會跳回去。
  void replaceElder(Elder updated) {
    final i = elders.indexWhere((e) => e.elderId == updated.elderId);
    if (i < 0) return;
    elders = [...elders.sublist(0, i), updated, ...elders.sublist(i + 1)];
  }

  /// 切換目前在看的長者，並記住到下次啟動。
  Future<void> selectElder(String elderId) async {
    selectedElderId = elderId;
    final account = _accountId;
    if (account == null) return;
    final p = await SharedPreferences.getInstance();
    await p.setString(_selectedElderKey(account), elderId);
  }

  /// 首次設定完成：寫入長者資料並標記**這個帳號**已完成，之後登入不再進 /setup。
  Future<void> saveSetup({
    required String name,
    required String nickname,
    required String lang,
    int? birthYear,
    String addressRegion = '',
    String hakkaDialect = HakkaDialect.defaultValue,
  }) async {
    elderName = name;
    elderNickname = nickname;
    elderBirthYear = birthYear;
    elderAddressRegion = addressRegion;
    elderHakkaDialect = hakkaDialect;
    this.lang = lang;
    _langChosen = true;
    setupDone = true;
    // 未登入時只留在記憶體：沒有 sub 就沒有帳號可以掛，寫成裝置層級的資料正是
    // 先前修掉的問題。註冊流程裡的 /setup（尚未登入）不走這裡，走
    // [savePendingSetup]——那條路徑才有 email 可以當寄放的依據。
    final account = _accountId;
    if (account == null) return;
    final p = await SharedPreferences.getInstance();
    await p.setString(_nameKey(account), name);
    await p.setString(_nicknameKey(account), nickname);
    await p.setString(_langKey(account), lang);
    // 沒填就不寫 key，讓它跟「填了空字串」分得出來（與 _langChosen 同一個道理）。
    if (birthYear != null) await p.setInt(_birthYearKey(account), birthYear);
    if (addressRegion.isNotEmpty) {
      await p.setString(_addressRegionKey(account), addressRegion);
    }
    await p.setString(_dialectKey(account), hakkaDialect);
    await p.setBool(_setupDoneKey(account), true);

    // 後端建立長者資料並綁定帳號
    try {
      await CareRepo.instance.createElder({
        'name': name,
        if (nickname.isNotEmpty) 'nickname': nickname,
        if (birthYear != null) 'birth_year': birthYear,
        if (addressRegion.isNotEmpty) 'address_region': addressRegion,
        'lang_preference': lang == 'hak' ? 'hak' : 'zh-TW',
        'hakka_dialect': hakkaDialect,
        'self_register': true,
      });
    } catch (_) {
      // 後端不可用時不擋首次設定流程
    }
  }

  /// 註冊流程中完成設定：把長輩資料暫存在 [email] 底下（見 [_kPendingSetupPrefix]）。
  ///
  /// 這裡**刻意不動**記憶體欄位與帳號旗標：現在還沒登入，這份資料還不屬於任何帳號，
  /// 提前寫進去就等於讓下一個在這台裝置登入的人繼承它。
  Future<void> savePendingSetup({
    required String email,
    required String name,
    required String nickname,
    required String lang,
    int? birthYear,
    String addressRegion = '',
    String hakkaDialect = HakkaDialect.defaultValue,
  }) async {
    final p = await SharedPreferences.getInstance();
    await p.setString(
      _pendingSetupKey(email),
      jsonEncode({
        'name': name,
        'nickname': nickname,
        'lang': lang,
        'birth_year': birthYear,
        'address_region': addressRegion,
        'hakka_dialect': hakkaDialect,
      }),
    );
  }

  /// 把 [savePendingSetup] 寄放的資料兌現到 [accountId] 這個帳號，並清掉暫存項。
  ///
  /// 由 `AuthService.signIn` 在第一次登入成功、拿到 sub 之後呼叫。沒有暫存項就什麼都不做
  /// （例如在別台裝置註冊、或註冊時選的是照護者）——這時 redirect 會把長者帶去 /setup
  /// 重填一次，那是刻意保留的退路。
  ///
  /// 兌現後直接 [loadForAccount]，讓記憶體狀態與剛寫進去的持久化一致：redirect 讀的是
  /// 記憶體裡的 [setupDone]，只寫 prefs 不重載會讓人再被送去 /setup 一次。
  Future<void> consumePendingSetup({
    required String email,
    required String accountId,
  }) async {
    final p = await SharedPreferences.getInstance();
    final key = _pendingSetupKey(email);
    final raw = p.getString(key);
    if (raw == null) return;

    // 寫壞或換過格式的暫存資料一律丟掉：這裡塞不進正確的資料，
    // 留著只會在每次登入重試一次；清掉之後由 /setup 重填，使用者仍走得下去。
    Map<String, dynamic>? data;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) data = decoded;
    } catch (_) {
      data = null;
    }
    if (data == null) {
      await p.remove(key);
      return;
    }

    final name = data['name'];
    final nickname = data['nickname'];
    final lang = data['lang'];
    final birthYear = data['birth_year'];
    final region = data['address_region'];
    final dialect = data['hakka_dialect'];
    await p.setString(_nameKey(accountId), name is String ? name : '');
    await p.setString(
        _nicknameKey(accountId), nickname is String ? nickname : '');
    await p.setString(_langKey(accountId), lang is String ? lang : 'zh-TW');
    // 舊格式的暫存項沒有這兩個 key，型別不符就當作沒填——不寫比寫一個猜的值好。
    if (birthYear is int) await p.setInt(_birthYearKey(accountId), birthYear);
    if (region is String && region.isNotEmpty) {
      await p.setString(_addressRegionKey(accountId), region);
    }
    if (dialect is String && dialect.isNotEmpty) {
      await p.setString(_dialectKey(accountId), dialect);
    }
    await p.setBool(_setupDoneKey(accountId), true);
    await p.remove(key);

    // 後端建立長者資料並綁定帳號（self_register=true 讓 pre-token trigger 下次能注入 elder_id）
    try {
      await CareRepo.instance.createElder({
        'name': name is String ? name : '',
        if (nickname is String && nickname.isNotEmpty) 'nickname': nickname,
        if (birthYear is int) 'birth_year': birthYear,
        if (region is String && region.isNotEmpty) 'address_region': region,
        if (lang is String) 'lang_preference': lang == 'hak' ? 'hak' : 'zh-TW',
        if (dialect is String && dialect.isNotEmpty) 'hakka_dialect': dialect,
        'self_register': true,
      });
    } catch (_) {
      // 後端不可用時不擋首次設定流程：本機資料已存好，下次啟動可重試
    }

    await loadForAccount(accountId);
  }

  /// email 一律 trim + 轉小寫，與 `DemoAuthBackend` 及 `AuthService` 的暫存 key 一致——
  /// 註冊打「A@Example.com 」、登入打「a@example.com」要指到同一筆暫存。
  static String _pendingSetupKey(String email) =>
      '$_kPendingSetupPrefix${email.trim().toLowerCase()}';

  /// 登出時把記憶體裡的狀態歸零。
  ///
  /// **不刪任何持久化資料**：所有 key 都綁 sub（見 [_nameKey] 等），不同帳號各讀
  /// 各的，本來就不會互相看到。原本連同「已完成設定」的旗標一起刪，是因為姓名那幾個
  /// 曾經是裝置層級的全域 key，不刪就會被下一個人繼承；代價卻是**同一個人登出再登入
  /// 也要重走一次首次設定**。綁 sub 之後這個取捨消失了，兩邊都成立。
  ///
  /// [accountId] 保留在簽名上是為了呼叫端的可讀性（`signOut` 要表達「清掉這個帳號的
  /// 狀態」），實作上已不需要它。
  Future<void> clearForAccount(String? accountId) async {
    // 記憶體欄位要回到預設值：單例活得比登入狀態久，不歸零的話，
    // 下一個人登入後在載入完成之前會先看到上一個人的資料。
    setupDone = false;
    elderName = '';
    elderNickname = '';
    elderBirthYear = null;
    elderAddressRegion = '';
    elderHakkaDialect = HakkaDialect.defaultValue;
    lang = 'zh-TW';
    _langChosen = false;
    textLang = 'zh-TW';
    me = null;
    elders = const [];
    selectedElderId = null;
    linkedCaregivers = const [];
    _accountId = null;
  }
}
