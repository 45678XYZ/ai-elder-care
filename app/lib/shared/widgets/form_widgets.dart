import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../theme/app_theme.dart';

/// 長者也讀得到的輸入框：字級 >=24sp、內距大、外框 2px（聚焦 3px）。
///
/// 登入／註冊／驗證碼／連結家人共用一份——這幾頁的輸入框只差在鍵盤型別與
/// 過濾規則，樣式各寫一次遲早會走鐘。
class BigTextField extends StatelessWidget {
  const BigTextField({
    super.key,
    required this.controller,
    this.hint,
    this.focusNode,
    this.enabled = true,
    this.obscureText = false,
    this.keyboardType,
    this.textInputAction,
    this.inputFormatters,
    this.textAlign = TextAlign.start,
    this.letterSpacing,
    this.onSubmitted,
  });

  final TextEditingController controller;

  /// 佔位文字。跟上方 label 重複時就別給——重複一次不會更清楚，
  /// 只會讓空欄位看起來像已經填了東西。
  final String? hint;
  final FocusNode? focusNode;
  final bool enabled;
  final bool obscureText;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final List<TextInputFormatter>? inputFormatters;
  final TextAlign textAlign;

  /// 號碼類輸入拉開字距比較不會看錯；一般文字不需要。
  final double? letterSpacing;
  final ValueChanged<String>? onSubmitted;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final style = text.headlineSmall?.copyWith(letterSpacing: letterSpacing);
    // 欄位不畫框：靠近白的底色加一層陰影從紙底浮起來，比框線乾淨。
    // 框只在聚焦時出現，聚焦指示不能省——它是鍵盤操作唯一的位置線索。
    return DecoratedBox(
      decoration: BoxDecoration(
        color: enabled ? AppColors.cardAlt : AppColors.track,
        borderRadius: const BorderRadius.all(AppRadius.field),
        boxShadow: enabled ? AppShadows.card : null,
      ),
      child: TextField(
        controller: controller,
        focusNode: focusNode,
        enabled: enabled,
        obscureText: obscureText,
        autocorrect: false,
        enableSuggestions: false,
        keyboardType: keyboardType,
        textInputAction: textInputAction,
        inputFormatters: inputFormatters,
        textAlign: textAlign,
        style: style,
        onSubmitted: onSubmitted,
        decoration: InputDecoration(
          hintText: hint,
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
        ),
      ),
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
              size: 32, color: isError ? AppColors.warnFg : AppColors.successFg),
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
      child: Text('智慧長照陪伴',
          style: text.labelLarge?.copyWith(color: Colors.white)),
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
