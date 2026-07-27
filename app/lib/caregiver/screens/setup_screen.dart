import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../shared/services/notification_service.dart';
import '../../shared/services/session_store.dart';
import '../../theme/app_theme.dart';

/// S1 `/setup` — 初次設定（照護者填寫，只在首次安裝出現一次）。
///
/// 照護者規格：字級 13–24sp、觸控 >=48dp。
/// §5.1 依據：語言（輸入方式）由照護者設定，長者端不切換，避免迷惑感。
class SetupScreen extends StatefulWidget {
  const SetupScreen({super.key});

  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends State<SetupScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _nicknameCtrl = TextEditingController();

  /// 驗證失敗時要捲回表單頂端——錯誤訊息在欄位下方，但使用者按的按鈕在畫面最下面，
  /// 不捲回去他只會看到「按了沒反應」。
  final _scrollCtrl = ScrollController();

  /// 語言偏好，對齊 api.md lang_preference：'zh-TW' | 'hak'。
  String _lang = 'zh-TW';

  @override
  void initState() {
    super.initState();
    // 稱呼／姓名變動時即時更新語言卡的範例句。
    _nameCtrl.addListener(_onNameChanged);
    _nicknameCtrl.addListener(_onNameChanged);
  }

  void _onNameChanged() => setState(() {});

  @override
  void dispose() {
    _nameCtrl.dispose();
    _nicknameCtrl.dispose();
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
          content: Text('請先填長輩姓名',
              style: Theme.of(context)
                  .textTheme
                  .bodyLarge
                  ?.copyWith(color: AppColors.onDark)),
        ),
      );
      return;
    }
    // TODO: 串接後 POST /elders 建立長者資料（name / nickname / lang_preference）。
    // 目前先持久化到本機：標記已完成首次設定並存長者資料，之後啟動不再進此畫面。
    await AppSession.instance.saveSetup(
      name: _nameCtrl.text.trim(),
      nickname: _nicknameCtrl.text.trim(),
      lang: _lang,
    );

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

    if (mounted) context.go('/');
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
                  textInputAction: TextInputAction.done,
                  decoration: _fieldDecoration(hint: '例如：阿蘭嬤'),
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
                    Text('※ 影響語音辨識，可在 照護者模式 › 管理 更改',
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
              color: selected ? AppColors.accentText : AppColors.border,
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
