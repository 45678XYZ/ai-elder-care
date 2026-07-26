import 'package:shared_preferences/shared_preferences.dart';

/// App 執行期的長者情境暫存（Demo 用）。
///
/// 目前尚無登入與 GET /elders，S1 首次設定把長者資料寫進這裡，
/// 供 S3 問候語與輸入路徑讀取。後端上線後改為由 /elders 載入並移除此暫存。
///
/// 另用 shared_preferences 持久化「是否已完成首次設定」與長者基本資料：
/// 首次安裝才進 /setup，之後啟動直接進角色選擇（見 [load]／[saveSetup]）。
class AppSession {
  AppSession._();
  static final AppSession instance = AppSession._();

  static const _kSetupDone = 'setup_done';
  static const _kName = 'elder_name';
  static const _kNickname = 'elder_nickname';
  static const _kLang = 'elder_lang';

  String elderName = '';
  String elderNickname = '';

  /// 語言偏好，對齊 api.md：'zh-TW' | 'hak'。決定長者端輸入路徑。
  String lang = 'zh-TW';

  /// 是否已完成首次設定；決定 App 啟動落點（見 main.dart）。
  bool setupDone = false;

  /// 客語裝置端 ASR 不支援，走錄音送後端；華語走裝置端辨識。
  bool get isHakka => lang == 'hak';

  /// 稱呼 fallback：暱稱 → 姓名 → 通用占位。
  String get displayName {
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
