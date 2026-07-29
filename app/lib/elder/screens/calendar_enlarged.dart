import 'package:flutter/material.dart';

import '../../shared/services/lunar_date.dart';
import '../../theme/app_theme.dart';
import '../widgets/almanac_face.dart';
import '../widgets/greeting_slot.dart';

/// 撕曆的放大檢視（規劃書 P1 / R2、R3、R4）。
///
/// 放大不只是「變大」，而是**資訊加碼**：小卡省略的干支全稱、節氣、完整農曆
/// 都在這裡補齊。小卡負責可辨識，這裡負責可讀。
///
/// 關閉有三個出口，缺一不可（規劃書 §2.3 問題三）：
/// 1. 底部大按鈕「✕ 關閉」——含文字、64dp 高，這是**主要**出口
/// 2. 點內容以外的遮罩
/// 3. Android 返回鍵（由 Navigator 預設處理）
///
/// 不用右上角小叉叉當唯一出口：那是需要學習的圖示，65+ 使用者實測常常找不到。
/// 也不提供雙指縮放——長者手部精細動作困難，全螢幕檢視已經夠清楚。
/// [aspectRatio] 是小卡那一面的長寬比。放大要維持**同一個比例**——
/// 拉成整個直式螢幕的話，同一份內容會被拉長成另一種版面，長輩等於看到兩張
/// 不一樣的日曆。所以放大 = 同比例放到畫面放得下的最大，不是填滿。
Future<void> showEnlargedDate(
  BuildContext context, {
  required DateTime now,
  required LunarDate lunar,
  required Color color,
  required double aspectRatio,
}) =>
    _showEnlarged(
      context,
      heroTag: 'calendar_card_hero',
      child: AspectRatio(
        aspectRatio: aspectRatio,
        child: _EnlargedDateBody(now: now, lunar: lunar, color: color),
      ),
    );

Future<void> showEnlargedGreeting(
  BuildContext context, {
  required DateTime now,
  required double aspectRatio,
}) =>
    _showEnlarged(
      context,
      heroTag: 'morning_image_hero',
      child: _EnlargedGreetingBody(now: now, fallbackAspectRatio: aspectRatio),
    );

Future<void> _showEnlarged(
  BuildContext context, {
  required String heroTag,
  required Widget child,
}) {
  // 系統開了「減少動態效果」就不做轉場，直接出現（規劃書 P3）。
  final reduceMotion = MediaQuery.of(context).disableAnimations;
  return showGeneralDialog<void>(
    context: context,
    barrierDismissible: true, // 出口 2：點遮罩
    barrierLabel: '關閉',
    barrierColor: Colors.black54,
    transitionDuration:
        reduceMotion ? Duration.zero : const Duration(milliseconds: 300),
    pageBuilder: (context, _, __) =>
        _EnlargedScaffold(heroTag: heroTag, child: child),
    transitionBuilder: (context, anim, _, page) => FadeTransition(
      opacity: CurvedAnimation(parent: anim, curve: Curves.easeOutCubic),
      child: page,
    ),
  );
}

/// 放大檢視的外框：內容拿到「螢幕扣掉關閉鈕」的整塊空間，**按原比例**放到最大。
///
/// 不拉伸填滿：拉伸會把同一份內容變成另一種版面（日曆會被拉長、早安圖會變形），
/// 放大的意思是同一張東西看得更清楚，不是換一張。
class _EnlargedScaffold extends StatelessWidget {
  const _EnlargedScaffold({required this.heroTag, required this.child});

  final String heroTag;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          // 整組（內容＋關閉鈕）一起置中，關閉鈕就**貼在內容下面**，
          // 而不是釘在畫面最底下離內容老遠——那樣看起來像兩件不相干的東西。
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Flexible(
                child: Hero(
                  tag: heroTag,
                  child: Material(
                    color: Colors.transparent,
                    child: child,
                  ),
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
              // 出口 1：主要出口，帶文字的大按鈕
              const _CloseButton(),
            ],
          ),
        ),
      ),
    );
  }
}

class _CloseButton extends StatelessWidget {
  const _CloseButton();

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return SizedBox(
      width: double.infinity,
      height: 64, // 規劃書指定；遠高於 60dp 觸控下限
      child: FilledButton.icon(
        onPressed: () => Navigator.of(context).pop(),
        icon: const Icon(Icons.close, size: 32),
        label: Text('關閉',
            style: text.headlineSmall?.copyWith(color: Colors.white)),
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.accentText,
          foregroundColor: Colors.white,
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(AppRadius.field),
          ),
        ).copyWith(
          overlayColor: const WidgetStatePropertyAll(AppColors.accentPressed),
        ),
      ),
    );
  }
}

/// 放大後的日期。版面跟小卡**一模一樣**（同一個 [AlmanacFace]），只有大小不同——
/// 放大檢視換一套排版的話，長輩等於要重新認一次這張日曆。
class _EnlargedDateBody extends StatelessWidget {
  const _EnlargedDateBody({
    required this.now,
    required this.lunar,
    required this.color,
  });

  final DateTime now;
  final LunarDate lunar;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: const BoxDecoration(
        color: AppColors.cardAlt,
        borderRadius: BorderRadius.all(AppRadius.cardLarge),
        boxShadow: AppShadows.cardRaised,
      ),
      child: AlmanacFace(date: now, lunar: lunar, color: color),
    );
  }
}

/// 放大後的早安圖。跟日曆一樣吃掉整個螢幕，找不到檔案就退回色塊。
class _EnlargedGreetingBody extends StatelessWidget {
  const _EnlargedGreetingBody(
      {required this.now, required this.fallbackAspectRatio});

  final DateTime now;

  /// 沒有圖檔時色塊要用的比例——照小卡那一面，維持「放大＝同一張變大」。
  /// 有圖的時候比例由**原圖**決定（`BoxFit.contain` 不裁切也不變形）。
  final double fallbackAspectRatio;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final g = GreetingSlot.of(now);
    return ClipRRect(
      borderRadius: const BorderRadius.all(AppRadius.cardLarge),
      child: Image.asset(
        g.asset,
        // contain：放大看的是**原圖**——不裁切也不變形，比例就是圖本身的比例。
        fit: BoxFit.contain,
        errorBuilder: (context, _, __) => AspectRatio(
          aspectRatio: fallbackAspectRatio,
          child: Container(
            color: AppColors.avatarBg,
            padding: const EdgeInsets.all(AppSpacing.xl),
            alignment: Alignment.center,
            child: FittedBox(
              fit: BoxFit.scaleDown,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(g.icon, size: 96, color: AppColors.avatarFg),
                  const SizedBox(height: AppSpacing.lg),
                  Text(g.label,
                      style: text.displayMedium
                          ?.copyWith(color: AppColors.avatarFg)),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
