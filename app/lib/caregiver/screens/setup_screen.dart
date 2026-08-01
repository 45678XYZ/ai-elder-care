import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../shared/models/elder.dart';
import '../../shared/services/notification_service.dart';
import '../../shared/services/session_store.dart';
import '../../theme/app_theme.dart';

/// S1 `/setup` — 初次設定（只在還沒有長輩資料時出現一次）。
///
/// 照護者規格：字級 13–24sp、觸控 >=48dp。
/// §5.1 依據：語言（輸入方式）由照護者設定，長者端不切換，避免迷惑感。
///
/// 同一個畫面服務兩種情境，靠 [email] 區分：
/// - **註冊流程中**（[email] 非 null，此時還沒登入）：註冊頁選了「長輩」後 push 進來，
///   完成設定 → 資料按 email 暫存 → 進驗證碼頁。
/// - **登入之後**（[email] 為 null）：這個帳號在本機沒有資料時的退路（換裝置登入），
///   完成設定 → 直接寫進帳號 → 交給 router 決定落點。
class SetupScreen extends StatefulWidget {
  const SetupScreen({super.key, this.email});

  /// 註冊流程中要暫存資料的信箱；已登入時為 null。
  ///
  /// 為什麼註冊流程要靠 email 而不是直接寫帳號：那時還沒有 Cognito `sub`，
  /// 沒有帳號可以掛（完整理由見 AppSession 的 `setup_pending_` 前綴說明）。
  final String? email;

  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends State<SetupScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _nicknameCtrl = TextEditingController();
  final _birthYearCtrl = TextEditingController();
  final _regionCtrl = TextEditingController();

  /// 驗證失敗時要捲回表單頂端——錯誤訊息在欄位下方，但使用者按的按鈕在畫面最下面，
  /// 不捲回去他只會看到「按了沒反應」。
  final _scrollCtrl = ScrollController();

  /// 語言偏好，對齊 api.md lang_preference：'zh-TW' | 'hak'。
  String _lang = 'zh-TW';

  /// 客語腔調。只有 [_lang] 是 `hak` 時才問得到，華語的長輩看不到這一區。
  ///
  /// 預設四縣與 api.md 一致——不預選任何一個會讓「還沒選」和「選了四縣」長得一樣，
  /// 而後端沒收到值時本來就是四縣，畫面該反映那個事實。
  HakkaDialect _dialect = HakkaDialect.sixian;

  /// 性別。**不預選**：後端這個欄位可為 null，而「還沒問到」與「長輩回答其他」
  /// 是兩件事，預設一個值等於替他決定，之後也分不出這筆問過沒有。
  /// 也因此不列為必填——問不出來就留空，不該卡住整個設定流程。
  Gender? _gender;

  @override
  void initState() {
    super.initState();
    // 稱呼／姓名變動時即時更新語言卡的範例句。
    _nameCtrl.addListener(_onNameChanged);
    _nicknameCtrl.addListener(_onNameChanged);
  }

  void _onNameChanged() => setState(() {});

  /// 出生年只擋「一定不對」的輸入，不猜使用者的意圖。
  ///
  /// 上限用今年而不是固定值：長輩不可能還沒出生。下限 1900 是一個不會誤擋任何
  /// 真實長輩、又能攔下明顯打錯（民國年、手滑多打一位）的界線——填 114 或 19488
  /// 都會被擋下來，而不是安靜地存成一個荒謬的年齡。
  String? _validateBirthYear(String? v) {
    final s = v?.trim() ?? '';
    if (s.isEmpty) return '請填出生年';
    final year = int.tryParse(s);
    if (year == null) return '請填西元年份，例如 1948';
    final thisYear = DateTime.now().year;
    if (year < 1900 || year > thisYear) return '請填 1900～$thisYear 之間的西元年';
    return null;
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _nicknameCtrl.dispose();
    _birthYearCtrl.dispose();
    _regionCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  /// 稱呼 fallback：優先暱稱 → 姓名 → 通用占位。
  String get _displayName {
    final nick = _nicknameCtrl.text.trim();
    if (nick.isNotEmpty) return nick;
    final name = _nameCtrl.text.trim();
    if (name.isNotEmpty) return name;
    return '阿公／阿嬤';
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) {
      // 錯誤訊息在畫面上方、按鈕在下方，只印紅字等於沒有回饋（§8：送出必有狀態回饋）。
      // 捲回頂端讓錯誤進入視野，再補一則提示說明為什麼沒有往下走。
      await _scrollCtrl.animateTo(
        0,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: AppColors.barDark,
          // 不再指名「姓名」：必填欄位現在有三個，講錯一個比不講更容易讓人
          // 往錯的地方看。真正的錯誤訊息在各欄位下方，這裡只負責說「還沒過」。
          content: Text('還有欄位沒填，請往上看紅字',
              style: Theme.of(context)
                  .textTheme
                  .bodyLarge
                  ?.copyWith(color: AppColors.onDark)),
        ),
      );
      return;
    }
    // 兩件事一起做：存本機（標記已完成首次設定，之後啟動不再進此畫面）＋
    // `POST /elders` 建立長者資料。實際送出在 session_store 的 saveSetup／
    // consumePendingSetup 裡——後端那一步失敗不擋流程，本機這份仍然算數。
    final email = widget.email;
    final name = _nameCtrl.text.trim();
    final nickname = _nicknameCtrl.text.trim();
    // validator 已經擋掉非數字與範圍外，這裡 parse 不會是 null。
    final birthYear = int.tryParse(_birthYearCtrl.text.trim());
    final region = _regionCtrl.text.trim();
    if (email != null) {
      // 註冊流程：還沒登入，資料先寄放在這個信箱底下，第一次登入時才兌現到帳號。
      await AppSession.instance.savePendingSetup(
        email: email,
        name: name,
        nickname: nickname,
        lang: _lang,
        birthYear: birthYear,
        addressRegion: region,
        hakkaDialect: _dialect.value,
        gender: _gender?.value,
      );
    } else {
      await AppSession.instance.saveSetup(
        name: name,
        nickname: nickname,
        lang: _lang,
        birthYear: birthYear,
        addressRegion: region,
        hakkaDialect: _dialect.value,
        gender: _gender?.value,
      );
    }

    // 在這裡才要通知權限，不在 App 一啟動就問——照護者剛設定完長輩資料，
    // 這時「要不要提醒吃藥」是有情境的問題，答應的機率也高得多。
    //
    // 不論結果如何都要往下走：使用者按了「完成設定」，設定就該完成。
    // 權限拿不到的後果是提醒不會響，不是把人卡在這一頁。
    try {
      await NotificationService.instance.requestPermission();
    } catch (_) {
      // 忽略：通知權限失敗不影響設定完成
    }

    if (!mounted) return;
    if (email != null) {
      // 註冊流程的下一站是驗證碼；信箱要帶過去（那一頁用它送驗證碼與重寄）。
      // 用 push 而不是 go：驗證碼頁的返回鍵要能退回這裡改資料。
      context.push('/auth/verify', extra: email);
    } else {
      // 已登入：落點不在這裡決定，交給 router 的 redirect（長者→今日頁）。
      context.go('/');
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          controller: _scrollCtrl,
          padding: AppSpacing.pageBody,
          child: Form(
            key: _formKey,
            autovalidateMode: AutovalidateMode.onUserInteraction,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('初次設定',
                    style: text.labelSmall
                        ?.copyWith(color: AppColors.inkSecondary)),
                const SizedBox(height: AppSpacing.sm),
                Text('建立長輩的基本資料', style: text.headlineSmall),
                const SizedBox(height: AppSpacing.xl),

                // 欄位一：長輩姓名（必填）
                Text('長輩姓名', style: text.labelLarge),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _nameCtrl,
                  style: text.bodyLarge,
                  textInputAction: TextInputAction.next,
                  decoration: _fieldDecoration(hint: '例如：陳阿蘭'),
                  validator: (v) =>
                      (v == null || v.trim().isEmpty) ? '請填長輩姓名' : null,
                ),
                const SizedBox(height: AppSpacing.lg),

                // 欄位二：稱呼（選填）
                Text('希望怎麼稱呼他', style: text.labelLarge),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _nicknameCtrl,
                  style: text.bodyLarge,
                  textInputAction: TextInputAction.next,
                  decoration: _fieldDecoration(hint: '例如：阿蘭嬤'),
                ),
                const SizedBox(height: AppSpacing.lg),

                // 欄位三：出生年（必填）
                //
                // 存年份不存年齡：年齡每年會變，存下來隔年就是錯的（api.md 的欄位
                // 也是 birth_year）。管理頁顯示歲數時由當年減出生年算。
                Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: AppSpacing.sm,
                  children: [
                    Text('出生年（西元）', style: text.labelLarge),
                    Text('※ 讓 AI 用合適的方式跟長輩對話',
                        style: text.bodySmall
                            ?.copyWith(color: AppColors.inkSecondary)),
                  ],
                ),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _birthYearCtrl,
                  style: text.bodyLarge,
                  keyboardType: TextInputType.number,
                  textInputAction: TextInputAction.next,
                  decoration: _fieldDecoration(hint: '例如：1948'),
                  validator: _validateBirthYear,
                ),
                const SizedBox(height: AppSpacing.lg),

                // 欄位四：居住地區（必填）
                //
                // 不只是基本資料——對話大腦查天氣要靠它（後端的 get_weather_forecast
                // 工具），沒有地區就答不出「明天會不會下雨」這類長輩最常問的問題。
                Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: AppSpacing.sm,
                  children: [
                    Text('居住地區', style: text.labelLarge),
                    Text('※ 長輩問天氣時要靠它',
                        style: text.bodySmall
                            ?.copyWith(color: AppColors.inkSecondary)),
                  ],
                ),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _regionCtrl,
                  style: text.bodyLarge,
                  textInputAction: TextInputAction.done,
                  decoration: _fieldDecoration(hint: '例如：台北市大安區'),
                  validator: (v) =>
                      (v == null || v.trim().isEmpty) ? '請填居住地區' : null,
                ),
                const SizedBox(height: AppSpacing.lg),

                // 欄位五：性別（選填）
                //
                // 標明「可不填」而不是靜靜地讓它選填：照護者看到一排選項會預設
                // 自己非選不可，問不到就卡在這裡不敢送出。
                Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: AppSpacing.sm,
                  children: [
                    Text('性別', style: text.labelLarge),
                    Text('※ 可不填',
                        style: text.bodySmall
                            ?.copyWith(color: AppColors.inkSecondary)),
                  ],
                ),
                const SizedBox(height: AppSpacing.sm),
                Wrap(
                  spacing: AppSpacing.sm,
                  runSpacing: AppSpacing.sm,
                  children: [
                    for (final g in Gender.values)
                      _DialectChip(
                        label: g.label,
                        selected: _gender == g,
                        // 再按一次取消選取：不預選就一定要有反悔的路，
                        // 否則手滑選到之後再也回不到「沒填」。
                        onTap: () =>
                            setState(() => _gender = _gender == g ? null : g),
                      ),
                  ],
                ),
                const SizedBox(height: AppSpacing.xl),

                // 語言單選。只決定語音路徑（ASR／TTS），不是介面語言——
                // 這件事註記在標題旁就夠了，使用者不需要知道背後的分工。
                // Wrap：textScaler 放大時註記換到下一行，不擠壞標題。
                Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: AppSpacing.sm,
                  children: [
                    Text('長輩說話的語言', style: text.labelLarge),
                    // 之後只有長輩自己改得了：管理頁那顆已經移除，理由見
                    // elder/widgets/lang_toggle.dart。這裡是唯一由照護者決定的
                    // 時機，所以要講清楚往後要去哪裡改。
                    Text('※ 影響語音辨識，之後長輩可在 長者模式 › 主頁 最下方自行更改',
                        style: text.bodySmall
                            ?.copyWith(color: AppColors.inkSecondary)),
                  ],
                ),
                const SizedBox(height: AppSpacing.sm),
                _LangCard(
                  title: '華語',
                  sample: '「$_displayName，早安！今天想聊聊嗎？」',
                  selected: _lang == 'zh-TW',
                  onTap: () => setState(() => _lang = 'zh-TW'),
                ),
                const SizedBox(height: AppSpacing.md),
                _LangCard(
                  title: '客語',
                  // 範例句為客語樣本，正式上線前請語言顧問校對。
                  sample: '「$_displayName，食飽吂？今晡日想聊聊無？」',
                  selected: _lang == 'hak',
                  onTap: () => setState(() => _lang = 'hak'),
                ),

                // 腔調只在選了客語時才問。華語家庭佔多數，讓他們滑過一個六選一的
                // 選單是純噪音；而且這個欄位對 lang_preference='zh-TW' 沒有意義。
                if (_lang == 'hak') ...[
                  const SizedBox(height: AppSpacing.xl),
                  Wrap(
                    crossAxisAlignment: WrapCrossAlignment.center,
                    spacing: AppSpacing.sm,
                    children: [
                      Text('客語腔調', style: text.labelLarge),
                      // 講「聽不懂」而不是「辨識率下降」：六腔各有獨立的 ASR 模型，
                      // 選錯不是口音不像，是整句話辨識失敗。照護者要知道這件事的
                      // 嚴重性，才會願意去問長輩而不是隨便選一個。
                      Text('※ 選錯會聽不懂長輩說的話，不確定就問長輩',
                          style: text.bodySmall
                              ?.copyWith(color: AppColors.inkSecondary)),
                    ],
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Wrap(
                    spacing: AppSpacing.sm,
                    runSpacing: AppSpacing.sm,
                    children: [
                      for (final d in HakkaDialect.values)
                        _DialectChip(
                          label: d.label,
                          selected: _dialect == d,
                          onTap: () => setState(() => _dialect = d),
                        ),
                    ],
                  ),
                ],
                const SizedBox(height: AppSpacing.xl),

                // CTA
                SizedBox(
                  width: double.infinity,
                  height: 56, // >=48dp
                  child: FilledButton(
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.accentText,
                      foregroundColor: Colors.white,
                      shape: const RoundedRectangleBorder(
                        borderRadius: BorderRadius.all(AppRadius.field),
                      ),
                    ).copyWith(
                      overlayColor:
                          const WidgetStatePropertyAll(AppColors.accentPressed),
                    ),
                    onPressed: _submit,
                    child: Text('完成設定', style: text.labelLarge),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  InputDecoration _fieldDecoration({required String hint}) => InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: AppColors.hint),
        filled: true,
        fillColor: AppColors.cardAlt,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        // 錯誤訊息在欄位下方，色 #7D281F（承 theme error）
        errorStyle: const TextStyle(color: Color(0xFF7D281F), fontSize: 13),
        // 平常不畫框，靠近白的填色與紙底區分；框只在聚焦與錯誤時出現——
        // 這兩種狀態一定要看得見，前者是鍵盤操作的位置線索，後者要指出是哪一欄有問題。
        enabledBorder: InputBorder.none,
        focusedBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(AppRadius.field),
          borderSide: BorderSide(color: AppColors.accent, width: 2),
        ),
        errorBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(AppRadius.field),
          borderSide: BorderSide(color: Color(0xFF7D281F)),
        ),
        focusedErrorBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(AppRadius.field),
          borderSide: BorderSide(color: Color(0xFF7D281F), width: 2),
        ),
      );
}

/// 語言選擇卡。選中 = 2px accentText 外框 + 實心圓點（§9：不只靠顏色）。
/// 腔調選項膠囊。六個並排放不下，用 Wrap 自己換行。
///
/// 不做成 [_LangCard] 那種附範例句的大卡片。除了六張大卡會把這一頁撐成兩倍長，
/// 更關鍵的是**範例句無從查證**：六腔的例句要有可靠來源才敢放，寫錯的客語比
/// 沒有客語更糟——照護者會照著念，長輩聽到不對的腔反而選錯。
///
/// 所以只列腔調名，由照護者去問長輩。
class _DialectChip extends StatelessWidget {
  const _DialectChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      button: true,
      selected: selected,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.pill),
        child: Container(
          constraints: const BoxConstraints(minHeight: 48), // 照護者模式下限
          padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md, vertical: AppSpacing.sm),
          decoration: BoxDecoration(
            color: selected ? AppColors.accentText : Colors.transparent,
            borderRadius: const BorderRadius.all(AppRadius.pill),
            border: Border.all(
              // 未選取走 borderInteractive 而不是 border：後者是輸入框線，
              // 壓在紙色底上只有 1.3:1，看不出這裡有一顆可以按的東西。
              color:
                  selected ? AppColors.accentText : AppColors.borderInteractive,
              width: selected ? 2 : 1,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              // 選中同時用實心底與勾表示，不只靠顏色（MASTER.md §6）。
              if (selected) ...[
                const Icon(Icons.check, size: 16, color: Colors.white),
                const SizedBox(width: 4),
              ],
              Text(label,
                  style: text.labelLarge?.copyWith(
                      color: selected ? Colors.white : AppColors.inkSecondary)),
            ],
          ),
        ),
      ),
    );
  }
}

class _LangCard extends StatelessWidget {
  const _LangCard({
    required this.title,
    required this.sample,
    required this.selected,
    required this.onTap,
  });

  final String title;
  final String sample;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      selected: selected,
      button: true,
      label: '$title${selected ? '，已選' : ''}',
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.card),
        child: Container(
          constraints: const BoxConstraints(minHeight: 48),
          padding: const EdgeInsets.all(AppSpacing.lg),
          decoration: BoxDecoration(
            color: AppColors.card,
            borderRadius: const BorderRadius.all(AppRadius.card),
            border: Border.all(
              color:
                  selected ? AppColors.accentText : AppColors.borderInteractive,
              width: selected ? 2 : 1,
            ),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 實心圓點：選中狀態的形狀線索
              Container(
                width: 22,
                height: 22,
                margin: const EdgeInsets.only(top: 2),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: selected ? AppColors.accentText : Colors.transparent,
                  border: Border.all(
                    color: selected ? AppColors.accentText : AppColors.chevron,
                    width: 2,
                  ),
                ),
                child: selected
                    ? const Icon(Icons.check, size: 14, color: Colors.white)
                    : null,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: text.titleMedium),
                    const SizedBox(height: 4),
                    Text(sample,
                        style: text.bodyMedium
                            ?.copyWith(color: AppColors.inkSecondary)),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
