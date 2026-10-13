| resource | method | route | A (owner) exp/act | B (other) exp/act | B random id | same error code | result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chat_saved | GET | `/api/v1/chat/sessions/{chat_saved_id}` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| chat_saved | GET | `/api/v1/chat/sessions/{chat_saved_id}/messages` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| chat_saved | POST | `/api/v1/chat/sessions/{chat_saved_id}/messages` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| chat_saved | POST | `/api/v1/chat/sessions/{chat_saved_id}/stream` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| chat_ephemeral | GET | `/api/v1/chat/sessions/{chat_ephemeral_id}` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| chat_ephemeral | GET | `/api/v1/chat/sessions/{chat_ephemeral_id}/messages` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| chat_ephemeral | POST | `/api/v1/chat/sessions/{chat_ephemeral_id}/messages` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| chat_ephemeral | POST | `/api/v1/chat/sessions/{chat_ephemeral_id}/stream` | 200/200 | 404/404 | 404 | yes (`session_not_found`) | PASS |
| journal | GET | `/api/v1/journal/{journal_id}` | 200/200 | 404/404 | 404 | yes (`journal_not_found`) | PASS |
| journal | PUT | `/api/v1/journal/{journal_id}` | 200/200 | 404/404 | 404 | yes (`journal_not_found`) | PASS |
| journal | DELETE | `/api/v1/journal/{journal_id}` | 204/204 | 404/404 | 404 | yes (`journal_not_found`) | PASS |
| mood | DELETE | `/api/v1/mood/{mood_id}` | 200/200 | 404/404 | 404 | yes (`mood_entry_not_found`) | PASS |
| assessment | GET | `/api/v1/assessments/phq9/history/{assessment_id}` | 200/200 | 404/404 | 404 | yes (`assessment_not_found`) | PASS |
| cbt | GET | `/api/v1/cbt/records/{cbt_id}` | 200/200 | 404/404 | 404 | yes (`cbt_not_found`) | PASS |
| cbt | DELETE | `/api/v1/cbt/records/{cbt_id}` | 204/204 | 404/404 | 404 | yes (`cbt_not_found`) | PASS |
| plan_item | POST | `/api/v1/plan/{plan_item_id}/skip` | 200/200 | 404/404 | 404 | yes (`plan_item_not_found`) | PASS |
| plan_item | POST | `/api/v1/plan/{plan_item_id}/done` | 200/200 | 404/404 | 404 | yes (`plan_item_not_found`) | PASS |
| assessment_cross_instrument | GET | `/api/v1/assessments/gad7/history/{assessment_cross_instrument_id}` | 404/404 | 404/404 | 404 | yes (`assessment_not_found`) | PASS |

Side checks: `{"any_listing_leak": false, "completion_id_reuse_by_other": {"code": "completion_conflict", "leaks_owner_data": false, "status": 409}, "export_of_b_contains_a_data": false, "export_of_b_status": 200, "listings_of_b": {"assessments": {"leaks": false, "status": 200}, "cbt": {"leaks": false, "status": 200}, "exercise_history": {"leaks": false, "status": 200}, "insights": {"leaks": false, "status": 200}, "journal": {"leaks": false, "status": 200}, "mood": {"leaks": false, "status": 200}}, "memory_of_b_contains_a_data": false}`

18/18 routes PASS; data leak via listings/export/memory: False
