import 'dart:convert';

import 'package:ai_elder_care/shared/services/api_client.dart';
import 'package:ai_elder_care/shared/services/api_repository.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// 刪除例行公事走的是哪一條路。
///
/// 這條路曾經因為 api.md 沒有刪除端點而暫用 `PATCH {active:false}` 代替。後端補上
/// `DELETE /routines/{id}` 的同時，也把 `active` 從 PATCH 的可更新欄位白名單移除了
/// ——舊寫法從那一刻起會吃 400 `INVALID_PARAMETER`，而畫面上看起來只是「刪不掉」。
/// 這種壞法沒有測試就看不出來，所以在這裡釘住實際送出的 method 與參數。
void main() {
  late List<http.BaseRequest> sent;

  /// 回一筆 `active:false` 的 routine——api.md 說 DELETE 回的是終態物件，不是空 body。
  http.Client backend({int status = 200}) {
    sent = [];
    return MockClient((req) async {
      sent.add(req);
      return http.Response(
        jsonEncode({
          'routine_id': 'rtn_1',
          'elder_id': 'eld_1',
          'title': '吃血壓藥',
          'type': 'medication',
          'schedule': {'freq': 'daily', 'time': '08:00'},
          'remind': true,
          'active': false,
        }),
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
    // 這個端點的冪等鍵在 query，不在 body（api.md）。
    expect(sent.single.url.queryParameters['client_request_id'], 'req-1');
  });

  test('回傳的終態物件解得出來，active 是 false', () async {
    final routine = await ApiClient(
      baseUrl: 'https://api.invalid',
      httpClient: backend(),
    ).deleteRoutine('rtn_1', clientRequestId: 'req-1');

    expect(routine.routineId, 'rtn_1');
    expect(routine.active, isFalse);
  });

  test('沒帶 client_request_id 時不送空參數，交給後端自行衍生', () async {
    await ApiClient(baseUrl: 'https://api.invalid', httpClient: backend())
        .deleteRoutine('rtn_1');

    expect(sent.single.url.queryParameters, isEmpty);
  });

  test('404 ROUTINE_NOT_FOUND 往上丟，不吞掉', () async {
    expect(
      () => repoWith(backend(status: 404))
          .deleteRoutine('rtn_1', clientRequestId: 'req-1'),
      throwsA(isA<Exception>()),
    );
  });
}
