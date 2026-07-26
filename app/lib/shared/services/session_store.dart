import 'package:shared_preferences/shared_preferences.dart';

import '../models/elder.dart';
import 'demo_data.dart';

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

  static const _kSetupDone = 'setup_done';
  static const _kName = 'elder_name';
  static const _kNickname = 'elder_nickname';
  static const _kLang = 'elder_lang';
  static const _kSelectedElder = 'selected_elder_id';

  String elderName = '';
  String elderNickname = '';

  /// 語言偏好，對齊 api.md：'zh-TW' | 'hak'。決定長者端輸入路徑。
  String lang = 'zh-TW';

  /// 是否已完成首次設定；決定 App 啟動落點（見 main.dart）。
  bool setupDone = false;

  /// 照護者可存取的長者（`GET /elders`）。尚未載入時為空。
  List<Elder> elders = const [];

  /// 目前在看哪一位長者；所有照護者畫面的 `elder_id` 都取自這裡。
  String? selectedElderId;

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
  bool get isHakka => (selectedElder?.langPreference ?? lang) == 'hak';

  /// 稱呼 fallback：選定長者的暱稱／姓名 → 首次設定填的 → 通用占位。
  String get displayName {
    final e = selectedElder;
    if (e != null) {
      if (e.nickname != null && e.nickname!.trim().isNotEmpty) {
        return e.nickname!.trim();
      }
      if (e.name.trim().isNotEmpty) return e.name.trim();
    }
    if (elderNickname.trim().isNotEmpty) return elderNickname.trim();
    if (elderName.trim().isNotEmpty) return elderName.trim();
    return '阿公／阿嬤';
  }

  /// App 啟動時載入已保存的設定狀態與長者資料（首次未設定則維持預設）。
  Future<void> load() async {
    final p = await SharedPreferences.getInstance();
    setupDone = p.getBool(_kSetupDone) ?? false;
    elderName = p.getString(_kName) ?? '';
    elderNickname = p.getString(_kNickname) ?? '';
    lang = p.getString(_kLang) ?? 'zh-TW';
    selectedElderId = p.getString(_kSelectedElder);
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
  ///
  /// TODO: 後端上線後改為 `api.getAllElders()`，此處的 DemoData 一併移除。
  Future<void> loadElders() async {
    final page = await DemoData.elders();
    elders = page.items;
    if (elders.isEmpty) {
      selectedElderId = null;
      return;
    }
    if (selectedElder == null) await selectElder(elders.first.elderId);
  }

  /// 切換目前在看的長者，並記住到下次啟動。
  Future<void> selectElder(String elderId) async {
    selectedElderId = elderId;
    final p = await SharedPreferences.getInstance();
    await p.setString(_kSelectedElder, elderId);
  }

  /// 首次設定完成：寫入長者資料並標記已完成，之後啟動不再進 /setup。
  /// TODO: 後端上線後改為 POST /elders，此持久化僅為登入前的 Demo 過渡。
  Future<void> saveSetup({
    required String name,
    required String nickname,
    required String lang,
  }) async {
    elderName = name;
    elderNickname = nickname;
    this.lang = lang;
    setupDone = true;
    final p = await SharedPreferences.getInstance();
    await p.setString(_kName, name);
    await p.setString(_kNickname, nickname);
    await p.setString(_kLang, lang);
    await p.setBool(_kSetupDone, true);
  }
}
