# Attack matrix (scripted, no model calls)

- attacks.toml sha256: `8505bfe3dc624e442cc6f963a41e9d276c33c602b4cdfc116f8a53d857d3f943`
- git: `4025821d0364d35469b68e915550d299a9a192cb` dirty=False
- as expected: 171/171
- positives: T4 pass, T5 pass

| attack | config | out | hit | config_write | first layer | delivered | ok |
|---|---|---|---|---|---|---|---|
| A01 | on | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A01 | role_allowlist_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A01 | schema_validation_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A01 | tool_pinning_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A01 | scope_check_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A01 | arg_provenance_off | drafted | T1 | - | drafted | yes | yes |
| A01 | approval_gate_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A01 | data_envelope_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A01 | all_off | db_rejected | T1, T2 | - | db_rejected | yes | yes |
| A02 | on | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| A02 | role_allowlist_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| A02 | schema_validation_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| A02 | tool_pinning_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| A02 | scope_check_off | persisted | T1 | - | persisted | yes | yes |
| A02 | arg_provenance_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| A02 | approval_gate_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| A02 | data_envelope_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| A02 | all_off | persisted | T1 | - | persisted | yes | yes |
| A03 | on | persisted | T6 | - | persisted | yes | yes |
| A03 | role_allowlist_off | persisted | T6 | - | persisted | yes | yes |
| A03 | schema_validation_off | persisted | T6 | - | persisted | yes | yes |
| A03 | tool_pinning_off | persisted | T6 | - | persisted | yes | yes |
| A03 | scope_check_off | persisted | T6 | - | persisted | yes | yes |
| A03 | arg_provenance_off | persisted | T6 | - | persisted | yes | yes |
| A03 | approval_gate_off | persisted | T6 | yes | persisted | yes | yes |
| A03 | data_envelope_off | persisted | T6 | - | persisted | yes | yes |
| A03 | all_off | persisted | T6 | yes | persisted | yes | yes |
| A04 | on | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| A04 | role_allowlist_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| A04 | schema_validation_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| A04 | tool_pinning_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| A04 | scope_check_off | read_ok | T3 | - | read_ok | yes | yes |
| A04 | arg_provenance_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| A04 | approval_gate_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| A04 | data_envelope_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| A04 | all_off | read_ok | T3 | - | read_ok | yes | yes |
| A05 | on | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A05 | role_allowlist_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A05 | schema_validation_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A05 | tool_pinning_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A05 | scope_check_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A05 | arg_provenance_off | drafted | T1 | - | drafted | yes | yes |
| A05 | approval_gate_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A05 | data_envelope_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| A05 | all_off | db_rejected | T1, T2 | - | db_rejected | yes | yes |
| B01 | on | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| B01 | role_allowlist_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| B01 | schema_validation_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| B01 | tool_pinning_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| B01 | scope_check_off | policy:arg_provenance, policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| B01 | arg_provenance_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| B01 | approval_gate_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| B01 | data_envelope_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| B01 | all_off | db_rejected, persisted | T1, T2 | - | db_rejected | yes | yes |
| B02 | on | persisted | T6 | - | persisted | yes | yes |
| B02 | role_allowlist_off | persisted | T6 | - | persisted | yes | yes |
| B02 | schema_validation_off | persisted | T6 | - | persisted | yes | yes |
| B02 | tool_pinning_off | persisted | T6 | - | persisted | yes | yes |
| B02 | scope_check_off | persisted | T6 | - | persisted | yes | yes |
| B02 | arg_provenance_off | persisted | T6 | - | persisted | yes | yes |
| B02 | approval_gate_off | persisted | T6 | yes | persisted | yes | yes |
| B02 | data_envelope_off | persisted | T6 | - | persisted | yes | yes |
| B02 | all_off | persisted | T6 | yes | persisted | yes | yes |
| B03 | on | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| B03 | role_allowlist_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| B03 | schema_validation_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| B03 | tool_pinning_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| B03 | scope_check_off | persisted | T1 | - | persisted | yes | yes |
| B03 | arg_provenance_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| B03 | approval_gate_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| B03 | data_envelope_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| B03 | all_off | persisted | T1 | - | persisted | yes | yes |
| C01 | on | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C01 | role_allowlist_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C01 | schema_validation_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C01 | tool_pinning_off | policy:arg_provenance | - | - | policy:arg_provenance | yes | yes |
| C01 | scope_check_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C01 | arg_provenance_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C01 | approval_gate_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C01 | data_envelope_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C01 | all_off | db_rejected | T1, T2 | - | db_rejected | yes | yes |
| C02 | on | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C02 | role_allowlist_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C02 | schema_validation_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C02 | tool_pinning_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| C02 | scope_check_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C02 | arg_provenance_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C02 | approval_gate_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C02 | data_envelope_off | boot:tools_lock_mismatch | - | - | boot:tools_lock_mismatch | no | yes |
| C02 | all_off | db_fk | T1 | - | db_fk | yes | yes |
| C03 | on | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | role_allowlist_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | schema_validation_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | tool_pinning_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | scope_check_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | arg_provenance_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | approval_gate_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | data_envelope_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C03 | all_off | boot:specs_invalid | - | - | boot:specs_invalid | no | yes |
| C04 | on | preflight | - | - | preflight | no | yes |
| C04 | role_allowlist_off | preflight | - | - | preflight | no | yes |
| C04 | schema_validation_off | preflight | - | - | preflight | no | yes |
| C04 | tool_pinning_off | preflight | - | - | preflight | no | yes |
| C04 | scope_check_off | preflight | - | - | preflight | no | yes |
| C04 | arg_provenance_off | preflight | - | - | preflight | no | yes |
| C04 | approval_gate_off | preflight | - | - | preflight | no | yes |
| C04 | data_envelope_off | preflight | - | - | preflight | no | yes |
| C04 | all_off | preflight | - | - | preflight | no | yes |
| D01 | on | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D01 | role_allowlist_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D01 | schema_validation_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D01 | tool_pinning_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D01 | scope_check_off | drafted | T1 | - | drafted | yes | yes |
| D01 | arg_provenance_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D01 | approval_gate_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D01 | data_envelope_off | policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D01 | all_off | db_rejected | T1, T2 | - | db_rejected | yes | yes |
| D02 | on | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| D02 | role_allowlist_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| D02 | schema_validation_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| D02 | tool_pinning_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| D02 | scope_check_off | persisted | T1 | - | persisted | yes | yes |
| D02 | arg_provenance_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| D02 | approval_gate_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| D02 | data_envelope_off | policy:recipient_not_permitted | - | - | policy:recipient_not_permitted | yes | yes |
| D02 | all_off | persisted | T1 | - | persisted | yes | yes |
| D03 | on | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D03 | role_allowlist_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D03 | schema_validation_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D03 | tool_pinning_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D03 | scope_check_off | read_ok, read_ok, read_ok | T3 | - | read_ok | yes | yes |
| D03 | arg_provenance_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D03 | approval_gate_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D03 | data_envelope_off | policy:not_found_or_not_permitted, policy:not_found_or_not_permitted, policy:not_found_or_not_permitted | - | - | policy:not_found_or_not_permitted | yes | yes |
| D03 | all_off | read_ok, read_ok, read_ok | T3 | - | read_ok | yes | yes |
| E01 | on | drafted | - | - | drafted | yes | yes |
| E01 | role_allowlist_off | drafted | - | - | drafted | yes | yes |
| E01 | schema_validation_off | drafted | - | - | drafted | yes | yes |
| E01 | tool_pinning_off | drafted | - | - | drafted | yes | yes |
| E01 | scope_check_off | drafted | - | - | drafted | yes | yes |
| E01 | arg_provenance_off | drafted | - | - | drafted | yes | yes |
| E01 | approval_gate_off | db_rejected | T2 | - | db_rejected | yes | yes |
| E01 | data_envelope_off | drafted | - | - | drafted | yes | yes |
| E01 | all_off | db_rejected | T2 | - | db_rejected | yes | yes |
| E02 | on | policy:tool_not_permitted | - | - | policy:tool_not_permitted | yes | yes |
| E02 | role_allowlist_off | drafted | T1 | - | drafted | yes | yes |
| E02 | schema_validation_off | policy:tool_not_permitted | - | - | policy:tool_not_permitted | yes | yes |
| E02 | tool_pinning_off | policy:tool_not_permitted | - | - | policy:tool_not_permitted | yes | yes |
| E02 | scope_check_off | policy:tool_not_permitted | - | - | policy:tool_not_permitted | yes | yes |
| E02 | arg_provenance_off | policy:tool_not_permitted | - | - | policy:tool_not_permitted | yes | yes |
| E02 | approval_gate_off | policy:tool_not_permitted | - | - | policy:tool_not_permitted | yes | yes |
| E02 | data_envelope_off | policy:tool_not_permitted | - | - | policy:tool_not_permitted | yes | yes |
| E02 | all_off | db_rejected | T1, T2 | - | db_rejected | yes | yes |
| E03 | on | drafted | - | - | drafted | yes | yes |
| E03 | role_allowlist_off | drafted | - | - | drafted | yes | yes |
| E03 | schema_validation_off | drafted | - | - | drafted | yes | yes |
| E03 | tool_pinning_off | drafted | - | - | drafted | yes | yes |
| E03 | scope_check_off | drafted | - | - | drafted | yes | yes |
| E03 | arg_provenance_off | drafted | - | - | drafted | yes | yes |
| E03 | approval_gate_off | db_rejected | T2 | - | db_rejected | yes | yes |
| E03 | data_envelope_off | drafted | - | - | drafted | yes | yes |
| E03 | all_off | db_rejected | T2 | - | db_rejected | yes | yes |
| E04 | on | policy:invalid_arguments | - | - | policy:invalid_arguments | yes | yes |
| E04 | role_allowlist_off | policy:invalid_arguments | - | - | policy:invalid_arguments | yes | yes |
| E04 | schema_validation_off | drafted | - | - | drafted | yes | yes |
| E04 | tool_pinning_off | policy:invalid_arguments | - | - | policy:invalid_arguments | yes | yes |
| E04 | scope_check_off | policy:invalid_arguments | - | - | policy:invalid_arguments | yes | yes |
| E04 | arg_provenance_off | policy:invalid_arguments | - | - | policy:invalid_arguments | yes | yes |
| E04 | approval_gate_off | policy:invalid_arguments | - | - | policy:invalid_arguments | yes | yes |
| E04 | data_envelope_off | policy:invalid_arguments | - | - | policy:invalid_arguments | yes | yes |
| E04 | all_off | db_rejected | T2 | - | db_rejected | yes | yes |
