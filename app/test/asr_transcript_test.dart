import 'package:e_hakka_care/shared/services/speech_service.dart';
import 'package:flutter_test/flutter_test.dart';

/// 逐字稿在同一輪裡不能無故變短。
///
/// 實機回報：華語講「我今天 11 點要去吃午餐」，錄音沒停，逐字稿還被削掉。
/// 原因是 Android 的辨識器會在同一輪裡重新分段——分段之後 `recognizedWords`
/// 從新的一段從頭算起，畫面上前半句當場消失，而**那份被削掉的文字會原樣送到
/// 後端當成長輩說的話**。時間與意圖通常都在前半句，削掉等於整句話沒用了。
///
/// 這一組同時釘住反面：不能因此把「二選一」做成「接起來」。接起來就是上一次
/// 那個 bug——「把我說的話改成客語把我說的話改成客語四海腔」。
void main() {
  group('mergeRecognized', () {
    test('邊講邊長：一律用新的', () {
      expect(mergeRecognized('我今天', '我今天11點'), '我今天11點');
      expect(mergeRecognized('我今天11點', '我今天11點要去吃午餐'), '我今天11點要去吃午餐');
    });

    test('重新分段只留後半句：留住整句，不採用被削掉的那份', () {
      expect(
        mergeRecognized('我今天11點要去吃午餐', '要去吃午餐'),
        '我今天11點要去吃午餐',
      );
    });

    test('重新分段只留前半句：同樣留住整句', () {
      expect(
        mergeRecognized('我今天11點要去吃午餐', '我今天11點'),
        '我今天11點要去吃午餐',
      );
    });

    test('辨識器修正用詞：長得不一樣就是修正，用新的', () {
      // 「十一點」→「11點」這種改寫不是削掉，舊的那份沒有包含新的那份。
      expect(mergeRecognized('我今天十一點要去', '我今天11點要去'), '我今天11點要去');
    });

    test('空字串不會把已經聽到的內容清掉', () {
      // 分段之間常常先來一次空的部分結果。照單全收的話畫面會閃一下空白，
      // 更糟的是最終結果是空的時候整句話就沒了。
      expect(mergeRecognized('我今天11點要去吃午餐', ''), '我今天11點要去吃午餐');
      expect(mergeRecognized('我今天11點要去吃午餐', '   '), '我今天11點要去吃午餐');
    });

    test('第一份文字直接採用', () {
      expect(mergeRecognized('', '我今天'), '我今天');
      expect(mergeRecognized('', ''), '');
    });

    test('永遠是二選一，不接起來', () {
      // 上一次修這題時用的是累積，結果辨識器一修正內容就變成重複句。
      const before = '把我說的話改成客語';
      const after = '把我說的話改成客語四海腔';
      final merged = mergeRecognized(before, after);
      expect(merged, after);
      expect(merged, isNot(contains('客語把我說的話')));
    });

    test('前後空白不影響判斷', () {
      expect(mergeRecognized('  我今天11點要去吃午餐 ', ' 要去吃午餐 '), '我今天11點要去吃午餐');
    });
  });
}
