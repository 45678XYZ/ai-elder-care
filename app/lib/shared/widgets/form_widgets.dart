import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../theme/app_theme.dart';

/// 長者也讀得到的輸入框：字級 >=24sp、內距大、外框 2px（聚焦 3px）。
///
/// 登入／註冊／驗證碼／連結家人共用一份——這幾頁的輸入框只差在鍵盤型別與
/// 過濾規則，樣式各寫一次遲早會走鐘。
///
/// 遮蔽狀態由欄位自己管（見 [showObscureToggle]），呼叫端不必為了一顆眼睛按鈕
/// 各自開一份 state。
class BigTextField extends StatefulWidget {
  const BigTextField({
    super.key,
    required this.controller,
    this.hint,
    this.focusNode,
    this.enabled = true,
    this.obscureText = false,
    this.showObscureToggle = false,
    this.keyboardType,
    this.textInputAction,
    this.inputFormatters,
    this.textAlign = TextAlign.start,
    this.letterSpacing,
    this.onChanged,
    this.onSubmitted,
  });

  final TextEditingController controller;

  /// 佔位文字。跟上方 label 重複時就別給——重複一次不會更清楚，
  /// 只會讓空欄位看起來像已經填了東西。
  final String? hint;
  final FocusNode? focusNode;
  final bool enabled;

  /// 初始是否遮蔽。開了 [showObscureToggle] 之後這只是預設值。
  final bool obscureText;

  /// 在欄位右側給一顆看得見／看不見的切換鈕。
  ///
  /// 密碼打錯重打對長輩是實質負擔，看得到自己打了什麼比藏起來重要；
  /// 觸控區給 60dp（長者規格），不是一般的 48dp。
  final bool showObscureToggle;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final List<TextInputFormatter>? inputFormatters;
  final TextAlign textAlign;

  /// 號碼類輸入拉開字距比較不會看錯；一般文字不需要。
  final double? letterSpacing;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;

  @override
  State<BigTextField> createState() => _BigTextFieldState();
}

class _BigTextFieldState extends State<BigTextField> {
  late bool _obscured = widget.obscureText;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final style =
        text.headlineSmall?.copyWith(letterSpacing: widget.letterSpacing);
    final showToggle = widget.showObscureToggle && widget.obscureText;
    // 欄位不畫框：靠近白的底色加一層陰影從紙底浮起來，比框線乾淨。
    // 框只在聚焦時出現，聚焦指示不能省——它是鍵盤操作唯一的位置線索。
    return DecoratedBox(
      decoration: BoxDecoration(
        color: widget.enabled ? AppColors.cardAlt : AppColors.track,
        borderRadius: const BorderRadius.all(AppRadius.field),
        boxShadow: widget.enabled ? AppShadows.card : null,
      ),
      child: TextField(
        controller: widget.controller,
        focusNode: widget.focusNode,
        enabled: widget.enabled,
        obscureText: _obscured,
        autocorrect: false,
        enableSuggestions: false,
        keyboardType: widget.keyboardType,
        textInputAction: widget.textInputAction,
        inputFormatters: widget.inputFormatters,
        textAlign: widget.textAlign,
        style: style,
        onChanged: widget.onChanged,
        onSubmitted: widget.onSubmitted,
        decoration: InputDecoration(
          hintText: widget.hint,
          hintStyle: style?.copyWith(color: AppColors.hint),
          filled: false,
          contentPadding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.lg, vertical: AppSpacing.lg),
          border: InputBorder.none,
          enabledBorder: InputBorder.none,
          disabledBorder: InputBorder.none,
          focusedBorder: const OutlineInputBorder(
            borderRadius: BorderRadius.all(AppRadius.field),
            borderSide: BorderSide(color: AppColors.accent, width: 3),
          ),
          suffixIcon: showToggle
              ? IconButton(
                  onPressed: widget.enabled
                      ? () => setState(() => _obscured = !_obscured)
                      : null,
                  // 眼睛圖示單看不一定讀得懂，語意標籤與長按提示都寫成完整句子
                  tooltip: _obscured ? '顯示密碼' : '隱藏密碼',
                  icon: Icon(
                    _obscured
                        ? Icons.visibility_outlined
                        : Icons.visibility_off_outlined,
                    size: 28,
                    color: AppColors.inkSecondary,
                  ),
                )
              : null,
          suffixIconConstraints:
              const BoxConstraints(minWidth: 60, minHeight: 60),
        ),
      ),
    );
  }
}

/// 欄位下方的說明或錯誤。
///
/// 錯誤就長在出問題的欄位下面，不丟到頁尾的 [FeedbackBanner]——長輩要在錯誤訊息與
/// 欄位之間自己連線很吃力。錯誤除了顏色另加 icon 並跳到 24sp（§9 狀態不只靠顏色，
/// 而且要看得到）；一般說明維持較小的次要字級。
class FieldNote extends StatelessWidget {
  const FieldNote(this.message, {super.key, this.isError = false});

  final String message;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    if (!isError) {
      return Text(message,
          style: text.bodyLarge?.copyWith(color: AppColors.inkSecondary));
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(Icons.error_outline, size: 28, color: AppColors.warnFg),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Text(message,
              style: text.headlineSmall?.copyWith(color: AppColors.warnFg)),
        ),
      ],
    );
  }
}

/// 長者規格的主要動作按鈕：72dp 高、24sp 白字，忙碌時顯示轉圈並禁用。
class BigButton extends StatelessWidget {
  const BigButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.busy = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return SizedBox(
      width: double.infinity,
      height: 72, // 長者模式觸控下限 60dp，主要動作再放寬
      child: FilledButton(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.accentText,
          foregroundColor: Colors.white,
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(AppRadius.field),
          ),
        ).copyWith(
          overlayColor: const WidgetStatePropertyAll(AppColors.accentPressed),
        ),
        onPressed: busy ? null : onPressed,
        child: busy
            ? const SizedBox(
                width: 28,
                height: 28,
                child: CircularProgressIndicator(
                    strokeWidth: 3, color: Colors.white),
              )
            : Text(label,
                style: text.headlineSmall?.copyWith(color: Colors.white)),
      ),
    );
  }
}

/// 送出結果。
///
/// 刻意不用 SnackBar：它字小、會自己消失，長輩來不及讀。訊息留在畫面上，
/// 成功與失敗除了顏色另以 icon 區分（§9 狀態不可只靠顏色傳遞）。
class FeedbackBanner extends StatelessWidget {
  const FeedbackBanner({
    super.key,
    required this.message,
    required this.isError,
  });

  final String message;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: isError ? AppColors.warnBg : AppColors.successBg,
        borderRadius: const BorderRadius.all(AppRadius.card),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(isError ? Icons.error_outline : Icons.check_circle_outline,
              size: 32,
              color: isError ? AppColors.warnFg : AppColors.successFg),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Text(
              message,
              style: text.headlineSmall?.copyWith(
                  color: isError ? AppColors.warnFg : AppColors.successFg),
            ),
          ),
        ],
      ),
    );
  }
}

/// 品牌標籤。登入／註冊／驗證碼三頁共用同一枚，讓人知道整段流程還在同一個 App 裡。
class AppLogoPill extends StatelessWidget {
  const AppLogoPill({super.key});

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg, vertical: AppSpacing.sm),
      decoration: const BoxDecoration(
        color: AppColors.accentText,
        borderRadius: BorderRadius.all(AppRadius.pill),
      ),
      child:
          Text('智慧長照陪伴', style: text.labelLarge?.copyWith(color: Colors.white)),
    );
  }
}

/// 次要動作的文字連結。仍保 60dp 觸控高度——長輩點不準，
/// 「看起來像連結」不代表可以縮小可點範圍。
class TextLink extends StatelessWidget {
  const TextLink({super.key, required this.label, required this.onTap});

  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return InkWell(
      onTap: onTap,
      borderRadius: const BorderRadius.all(AppRadius.pill),
      child: Container(
        constraints: const BoxConstraints(minHeight: 60),
        alignment: Alignment.center,
        padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg, vertical: AppSpacing.md),
        child: Text(
          label,
          textAlign: TextAlign.center,
          style: text.headlineSmall?.copyWith(
            color: AppColors.accentText,
            decoration: TextDecoration.underline,
            decorationColor: AppColors.accentText,
          ),
        ),
      ),
    );
  }
}

/// 條款同意勾選：方框（非圓點，避免跟 [BigChoiceCard] 的身分選擇搞混）+ 說明文字。
///
/// 目前用在註冊頁的「同意使用者同意機制與資料保留政策」——選取狀態除了外框變色，
/// 還加對勾圖示（§9 狀態不可只靠顏色傳遞），觸控區整列都能點，不必精準點中方框本身。
class ConsentCheckbox extends StatelessWidget {
  const ConsentCheckbox({
    super.key,
    required this.checked,
    required this.label,
    required this.onChanged,
  });

  final bool checked;
  final String label;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      checked: checked,
      button: true,
      label: '$label${checked ? '，已勾選' : ''}',
      child: InkWell(
        onTap: () => onChanged(!checked),
        borderRadius: const BorderRadius.all(AppRadius.card),
        child: Container(
          constraints: const BoxConstraints(minHeight: 60),
          padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md, vertical: AppSpacing.sm),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 28,
                height: 28,
                margin: const EdgeInsets.only(top: 2),
                decoration: BoxDecoration(
                  borderRadius: const BorderRadius.all(Radius.circular(6)),
                  color: checked ? AppColors.accentText : Colors.transparent,
                  border: Border.all(
                    color: checked ? AppColors.accentText : AppColors.chevron,
                    width: 2,
                  ),
                ),
                child: checked
                    ? const Icon(Icons.check, size: 18, color: Colors.white)
                    : null,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(child: Text(label, style: text.headlineSmall)),
            ],
          ),
        ),
      ),
    );
  }
}

/// 長者規格的返回：不用 AppBar 的小箭頭，給看得見、按得到的區塊。
class BigBackButton extends StatelessWidget {
  const BigBackButton({super.key, required this.onTap, this.label = '回上一頁'});

  final VoidCallback onTap;
  final String label;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return InkWell(
      onTap: onTap,
      borderRadius: const BorderRadius.all(AppRadius.pill),
      child: Container(
        constraints: const BoxConstraints(minHeight: 60),
        padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md, vertical: AppSpacing.sm),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.arrow_back, size: 32, color: AppColors.ink),
            const SizedBox(width: AppSpacing.sm),
            Text(label, style: text.headlineSmall),
          ],
        ),
      ),
    );
  }
}

/// 可選取的大卡片：目前用在註冊頁問「你是長輩還是家人」。
///
/// 樣式與觸控尺寸比照 `caregiver/screens/setup_screen.dart` 裡的私有 `_LangCard`
/// （選取 = 2px accentText 外框 + 淺底 + 實心圓點，§9 狀態不只靠顏色傳遞），
/// 但字級走長者規格：註冊頁在登入前，還不知道對面是長輩還是家人，一律照長者做——
/// 大字對照護者不會不好用，反過來則會。因此觸控下限取 60dp 而非 48dp。
///
/// TODO(refactor): `_LangCard` 之後應該收斂過來用這個元件。目前沒動它，是因為它多了
/// 範例句、且吃照護者規格（48dp／小字），一併改會把「首次設定」的版面也翻掉；
/// 等這個元件補上 `subtitle` 與尺寸級別後再合。
class BigChoiceCard extends StatelessWidget {
  const BigChoiceCard({
    super.key,
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      selected: selected,
      button: true,
      label: '$label${selected ? '，已選' : ''}',
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.card),
        child: Container(
          width: double.infinity,
          constraints: const BoxConstraints(minHeight: 60),
          padding: const EdgeInsets.all(AppSpacing.lg),
          decoration: BoxDecoration(
            // 選取時底色也換一階：外框在紙底上不夠顯眼，長輩要能一眼看出選了哪張。
            color: selected ? AppColors.avatarBg : AppColors.card,
            borderRadius: const BorderRadius.all(AppRadius.card),
            border: Border.all(
              color: selected ? AppColors.accentText : AppColors.border,
              width: selected ? 2 : 1,
            ),
          ),
          child: Row(
            children: [
              // 實心圓點：選取狀態的形狀線索
              Container(
                width: 28,
                height: 28,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: selected ? AppColors.accentText : Colors.transparent,
                  border: Border.all(
                    color: selected ? AppColors.accentText : AppColors.chevron,
                    width: 2,
                  ),
                ),
                child: selected
                    ? const Icon(Icons.check, size: 18, color: Colors.white)
                    : null,
              ),
              const SizedBox(width: AppSpacing.md),
              Icon(icon, size: 32, color: AppColors.avatarFg),
              const SizedBox(width: AppSpacing.md),
              // Expanded：兩倍字級時文字換行，不把卡片撐破。
              Expanded(child: Text(label, style: text.headlineSmall)),
            ],
          ),
        ),
      ),
    );
  }
}
