# This file and its contents are licensed under the Timescale License.
# Please see the included NOTICE for copyright information and
# LICENSE-TIMESCALE for a copy of the license.

# Test create_distributed_restore_point() concurrency cases
#
setup
{
	CREATE OR REPLACE FUNCTION waitpoint_locks(tag TEXT) RETURNS bigint AS
	$$
		SELECT count(*) FROM pg_locks WHERE objid = debug_waitpoint_id(tag);
	$$ LANGUAGE SQL;

	CREATE OR REPLACE FUNCTION remote_txn_locks() RETURNS bigint AS
	$$
		SELECT count(*) FROM pg_locks WHERE relation = '_timescaledb_catalog.remote_txn'::regclass;
	$$ LANGUAGE SQL;

	CREATE OR REPLACE FUNCTION foreign_server_locks() RETURNS bigint AS
	$$
		SELECT count(*) FROM pg_locks WHERE relation = 'pg_catalog.pg_foreign_server'::regclass;
	$$ LANGUAGE SQL;

	CREATE TABLE IF NOT EXISTS disttable(time timestamptz NOT NULL, device int, temp float);
}
setup { SELECT true AS delete_data_node FROM delete_data_node('data_node_4', if_exists => true); }
setup { SELECT node_name FROM add_data_node('data_node_1', host => 'localhost', database => 'cdrp_1', if_not_exists => true); }
setup { SELECT node_name FROM add_data_node('data_node_2', host => 'localhost', database => 'cdrp_2', if_not_exists => true); }
setup { SELECT node_name FROM add_data_node('data_node_3', host => 'localhost', database => 'cdrp_3', if_not_exists => true); }
setup { SELECT created FROM create_distributed_hypertable('disttable', 'time', 'device', data_nodes => ARRAY['data_node_1', 'data_node_2', 'data_node_3']); }

teardown
{
   DROP TABLE disttable;
}

# create distributed restore point
session "s1"
setup
{
	SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
	SET application_name = 's1';
	SET client_min_messages = 'ERROR';
}
step "s1_create_dist_rp" { SELECT restore_point > pg_lsn('0/0') as valid_lsn FROM create_distributed_restore_point('s1_test'); }

# concurrent remote transaction
session "s2"
setup
{
	SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
	SET application_name = 's2';
	SET client_min_messages = 'ERROR';
}
step "s2_create_dist_rp" { SELECT restore_point > pg_lsn('0/0') as valid_lsn FROM create_distributed_restore_point('s2_test'); }
step "s2_insert"         { INSERT INTO disttable VALUES ('2019-08-02 10:45', 0, 0.0); }
step "s2_begin"          { BEGIN; }
step "s2_commit"         { COMMIT; }
step "s2_create_dist_ht" {
	CREATE TABLE disttable2(time timestamptz NOT NULL, device int, temp float);
	SELECT created FROM create_distributed_hypertable('disttable2', 'time', 'device');
}
step "s2_drop_dist_ht"   { DROP TABLE disttable2; }
step "s2_dist_exec"      { CALL distributed_exec('SELECT true;', transactional => true); }

# locking session
session "s3"
setup
{
	SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
	SET application_name = 's3';
	SET client_min_messages = 'ERROR';
}
step "s3_lock_enable"   { SELECT debug_waitpoint_enable('create_distributed_restore_point_lock'); }
step "s3_lock_release"  { SELECT debug_waitpoint_release('create_distributed_restore_point_lock'); }
step "s3_lock_count"    {
	SELECT waitpoint_locks('create_distributed_restore_point_lock') as cdrp_locks, 
	       remote_txn_locks() as remote_txn_locks;
}

# case 1: new transaction DML/commit during the create_distributed_restore_point()
permutation "s3_lock_enable" "s1_create_dist_rp" "s2_insert" "s3_lock_count" "s3_lock_release"

# case 2: ongoing transaction DML/commit during the create_distributed_restore_point()
permutation "s2_begin" "s2_insert" "s3_lock_enable" "s1_create_dist_rp" "s3_lock_count" "s2_commit" "s3_lock_count" "s3_lock_release"

# case 3: concurrent create_distributed_restore_point() call
permutation "s3_lock_enable" "s1_create_dist_rp" "s2_create_dist_rp" "s3_lock_count" "s3_lock_release"

# case 4: concurrent distributed_exec() call during the the create_distributed_restore_point()
permutation "s3_lock_enable" "s1_create_dist_rp" "s2_dist_exec" "s3_lock_count" "s3_lock_release"

# case 5: concurrent create_distributed_hypertable() during the the create_distributed_restore_point()
permutation "s3_lock_enable" "s1_create_dist_rp" "s2_create_dist_ht" "s3_lock_count" "s3_lock_release"

# case 6: concurrent DDL/commit during the create_distributed_restore_point()
permutation "s3_lock_enable" "s1_create_dist_rp" "s2_drop_dist_ht" "s3_lock_count" "s3_lock_release"
