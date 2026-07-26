import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

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
    if (!_formKey.currentState!.validate()) return;
    // TODO: 串接後 POST /elders 建立長者資料（name / nickname / lang_preference）。
    // 目前先持久化到本機：標記已完成首次設定並存長者資料，之後啟動不再進此畫面。
    await AppSession.instance.saveSetup(
      name: _nameCtrl.text.trim(),
      nickname: _nicknameCtrl.text.trim(),
      lang: _lang,
    );
    if (mounted) context.go('/');
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: AppSpacing.pageBody,
          child: Form(
            key: _formKey,
            autovalidateMode: AutovalidateMode.onUserInteraction,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('初次設定 · 由照護者填寫',
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
                  decoration: _fieldDecoration(hint: '例如：阿蘭嬤（留空就用姓名）'),
                ),
                const SizedBox(height: AppSpacing.xl),

                // 語言單選（輸入方式）
                Text('長輩使用的語言', style: text.labelLarge),
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
                const SizedBox(height: AppSpacing.md),
                Text(
                  '長者畫面只會出現這一種語言，不顯示切換按鈕；日後可在照護者 › 管理 › 長輩偏好調整。',
                  style:
                      text.bodySmall?.copyWith(color: AppColors.inkSecondary),
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
        hintStyle: const TextStyle(color: AppColors.chevron),
        filled: true,
        fillColor: AppColors.card,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        // 錯誤訊息在欄位下方，色 #7D281F（承 theme error）
        errorStyle: const TextStyle(color: Color(0xFF7D281F), fontSize: 13),
        enabledBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(AppRadius.field),
          borderSide: BorderSide(color: AppColors.border),
        ),
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
