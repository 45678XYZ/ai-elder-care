import 'package:flutter/material.dart';

import '../../theme/app_theme.dart';
import '../services/api_exception.dart';

/// 非同步資料的四態外殼：loading／error／empty／success。
///
/// MASTER.md §8 要求非同步畫面三態都要畫、§13 要求 >300ms 的操作要有可見 loading，
/// 而不是只畫成功的樣子。這裡把四態集中一次寫好，畫面只描述成功時長怎樣。
///
/// error 態一定給重試按鈕（§8 錯誤要有復原路徑）；訊息取 [ApiException.message]，
/// 那是後端 `error.message` 或連線失敗的說明，可直接顯示。
class AsyncView<T> extends StatelessWidget {
  const AsyncView({
    super.key,
    required this.future,
    required this.builder,
    required this.onRetry,
    this.isEmpty,
    this.emptyIcon = Icons.inbox_outlined,
    this.emptyText = '目前沒有資料',
    this.elderMode = false,
  });

  final Future<T>? future;
  final Widget Function(BuildContext, T) builder;

  /// 重試：呼叫端重新建立 future 並 setState。
  final VoidCallback onRetry;

  /// 判斷資料是否為空；不給就一律當作有資料。
  final bool Function(T)? isEmpty;

  final IconData emptyIcon;
  final String emptyText;

  /// 長者模式：字級與觸控放大（內文 >=24sp、按鈕 >=60dp）。
  final bool elderMode;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<T>(
      future: future,
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return _Centered(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const CircularProgressIndicator(color: AppColors.accent),
                const SizedBox(height: AppSpacing.lg),
                Text('載入中…', style: _bodyStyle(context)),
              ],
            ),
          );
        }

        if (snap.hasError) {
          final e = snap.error;
          final message = e is ApiException ? e.message : '載入失敗，請稍後再試。';
          return _Centered(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // 狀態不只靠顏色：icon + 文字（§6）
                const Icon(Icons.cloud_off_outlined,
                    size: 44, color: AppColors.inkSecondary),
                const SizedBox(height: AppSpacing.md),
                Text(message,
                    textAlign: TextAlign.center, style: _bodyStyle(context)),
                const SizedBox(height: AppSpacing.lg),
                SizedBox(
                  height: elderMode ? 60 : 48,
                  child: OutlinedButton.icon(
                    onPressed: onRetry,
                    icon: const Icon(Icons.refresh),
                    label: Text('重新載入', style: _labelStyle(context)),
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

        final data = snap.data as T;
        if (isEmpty?.call(data) ?? false) {
          return _Centered(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(emptyIcon, size: 44, color: AppColors.chevron),
                const SizedBox(height: AppSpacing.md),
                Text(emptyText,
                    textAlign: TextAlign.center,
                    style: _bodyStyle(context)
                        ?.copyWith(color: AppColors.inkSecondary)),
              ],
            ),
          );
        }

        return builder(context, data);
      },
    );
  }

  TextStyle? _bodyStyle(BuildContext c) {
    final t = Theme.of(c).textTheme;
    return elderMode ? t.headlineSmall : t.bodyLarge;
  }

  TextStyle? _labelStyle(BuildContext c) {
    final t = Theme.of(c).textTheme;
    return elderMode ? t.headlineSmall : t.labelLarge;
  }
}

class _Centered extends StatelessWidget {
  const _Centered({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.xl),
          child: child,
        ),
      );
}
