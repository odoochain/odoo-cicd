CREATE OR REPLACE FUNCTION func_trigger_test_runs_at_commit()
RETURNS trigger AS
$BODY$
DECLARE testrun record;
DECLARE counted int;
DECLARE var_commit_id int;

BEGIN
    SELECT id, state, branch_id, commit_id
	INTO testrun
	FROM cicd_test_run
	WHERE id = NEW.id;


	IF testrun.state = 'running' THEN
		SELECT
			count(*)
		INTO
			counted
		FROM
			cicd_test_run
		WHERE
			cicd_test_run.commit_id = testrun.commit_id
		AND
			id <> testrun.id
		AND
			state = 'running';

		IF counted > 0 THEN
			RAISE EXCEPTION 'Cannot start two test runs for same commit';
		END IF;

	END IF;

    RETURN NEW;
END
$BODY$
    LANGUAGE 'plpgsql' SECURITY INVOKER
;

DROP TRIGGER IF EXISTS trigger_func_trigger_test_runs_at_commit ON cicd_test_run;

CREATE CONSTRAINT TRIGGER trigger_func_trigger_test_runs_at_commit
AFTER UPDATE ON cicd_test_run DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE PROCEDURE func_trigger_test_runs_at_commit();