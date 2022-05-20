CREATE OR REPLACE FUNCTION func_trigger_test_runs_at_commit()
RETURNS trigger AS
$BODY$
DECLARE testrun record;
DECLARE counted int;

BEGIN
    SELECT id, state, branch_id, commit_id
	INTO testrun
	FROM test_run
	WHERE id = NEW.id;

	IF test_run.state = 'running' THEN
		SELECT count(*)
		INTO counted
		FROM test_run
		WHERE branch_id = test_run.branch_id and commit_id = testrun.commit_id
		AND state = 'running';

		IF counted > 1 THEN
			RAISE EXCEPTION 'Cannot start two test runs for same commit';
		END IF;

	END IF;

    RETURN NEW;
END
$BODY$
    LANGUAGE 'plpgsql' SECURITY INVOKER
;

DROP TRIGGER IF EXISTS trigger_func_trigger_test_runs_at_commit ON test_run;

CREATE CONSTRAINT TRIGGER trigger_func_trigger_test_runs_at_commit
AFTER UPDATE ON test_run DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE PROCEDURE func_trigger_test_runs_at_commit();