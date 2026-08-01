import 'dart:convert';

import 'package:e_hakka_care/shared/services/api_client.dart';
import 'package:e_hakka_care/shared/services/api_repository.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// 刪除例行公事走的是哪一條路。
///
/// 這條路曾經因為 api.md 沒有刪除端點而暫用 `PATCH {active:false}` 代替。後端補上
/// `DELETE /routines/{id}` 的同時，也把 `active` 從 PATCH 的可更新欄位白名單移除了
/// ——舊寫法從那一刻起會吃 400 `INVALID_PARAMETER`，而畫面上看起來只是「刪不掉」。
///
/// 這個端點的契約前後改過三次（終態版本 → 無冪等硬刪 → tombstone 冪等硬刪），
/// 每一次 App 端都是「編譯得過但行為錯」，所以這裡釘的是**實際送出去的東西**：
/// method、路徑，以及 `client_request_id` 有沒有真的出現在 query 上。
///
/// 現行契約（api.md「DELETE /routines」）：
/// - `client_request_id` **必填且放 query**，沒帶回 400 `MISSING_REQUEST_ID`
/// - 回應是 `{"deleted": true, "routine_id": ...}`，不是 routine 物件
void main() {
  late List<http.BaseRequest> sent;

  /// 現行後端的刪除回應：只確認刪掉了，不回物件。
  http.Client backend({int status = 200}) {
    sent = [];
    return MockClient((req) async {
      sent.add(req);
      return http.Response(
        jsonEncode({'deleted': true, 'routine_id': 'rtn_1'}),
        status,
        headers: {'content-type': 'application/json; charset=utf-8'},
      );
    });
  }

  ApiRepository repoWith(http.Client client) => ApiRepository(
        client: ApiClient(baseUrl: 'https://api.invalid', httpClient: client),
      );

  test('刪除送 DELETE /routines/{id}，不是 PATCH active:false', () async {
    await repoWith(backend()).deleteRoutine('rtn_1', clientRequestId: 'req-1');

    expect(sent.single.method, 'DELETE');
    expect(sent.single.url.path, '/v1/routines/rtn_1');
  });

  test('client_request_id 真的送到 query 上', () async {
    // 這一條是為了一個實際發生過的壞法：`ApiRepository` 收下 clientRequestId
    // 之後直接丟掉，沒有轉交給 ApiClient。編譯過、型別對、測試若只驗 method
    // 也會過，但後端收不到 id 就回 400——畫面上只看得到「刪除失敗」。
    await repoWith(backend()).deleteRoutine('rtn_1', clientRequestId: 'req-1');

    expect(sent.single.url.queryParameters['client_request_id'], 'req-1');
  });

  test('回應不當成 routine 物件解析', () async {
    // 後端回的是 {"deleted": true}，沒有 title／schedule 等欄位。
    // 若哪天有人把回傳型別改回 Routine，這裡會在解析時炸開。
    await expectLater(
      ApiClient(baseUrl: 'https://api.invalid', httpClient: backend())
          .deleteRoutine('rtn_1', clientRequestId: 'req-1'),
      completes,
    );
  });

  test('404 ROUTINE_NOT_FOUND 往上丟，不吞掉', () async {
    expect(
      () => repoWith(backend(status: 404))
          .deleteRoutine('rtn_1', clientRequestId: 'req-1'),
      throwsA(isA<Exception>()),
    );
  });

  test('409 IDEMPOTENCY_CONFLICT 也往上丟', () async {
    // 換一個 client_request_id 去刪已經刪掉的那筆會拿到 409（tombstone 還在）。
    // 這不是「刪成功」，不能被當成沒事——照護者需要知道畫面跟後端不一致。
    expect(
      () => repoWith(backend(status: 409))
          .deleteRoutine('rtn_1', clientRequestId: 'req-2'),
      throwsA(isA<Exception>()),
    );
  });
}
