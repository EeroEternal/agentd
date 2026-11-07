import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentd import guest_agent, policy, tasks, vm_manager


class PolicyTests(unittest.TestCase):
    def test_host_and_vm_are_denied_by_default(self):
        with self.assertRaises(PermissionError):
            policy.authorize("exec", {"argv": ["id"]})
        with self.assertRaises(PermissionError):
            policy.authorize("vm_run", {"kernel": "/x", "disk": "/y"})

    def test_workspace_escape_is_denied(self):
        with self.assertRaises(PermissionError):
            policy.authorize("file_write", {"path": "/var/lib/agentos/workspace/../escape", "content": "x"})

    def test_explicit_capabilities(self):
        policy.authorize("exec", {"argv": ["id"]}, {"host": True})
        policy.authorize("vm_run", {"kernel": "/x", "disk": "/y"}, {"vm": True})
        policy.authorize("file_write", {"path": "/var/lib/agentos/workspace/ok.txt", "content": "ok"})


class TaskTests(unittest.TestCase):
    def test_history_download_and_undo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "before.txt").write_text("before")
            with patch.object(tasks, "TASKS", root / "tasks"), patch.object(tasks, "WORKSPACE", workspace):
                task_id = tasks.start("test")
                (workspace / "before.txt").write_text("changed")
                (workspace / "new.txt").write_text("new")
                tasks.finish(task_id, {"result": "done", "steps": [{"method": "cell_exec", "result": {}}]})
                self.assertEqual(tasks.last()["goal"], "test")
                self.assertEqual(tasks.download("new.txt"), b"new")
                self.assertEqual(tasks.undo_last()["status"], "undone")
                self.assertEqual((workspace / "before.txt").read_text(), "before")
                self.assertFalse((workspace / "new.txt").exists())


class VmTests(unittest.TestCase):
    def test_qemu_has_virtio_serial_agent(self):
        with patch.object(vm_manager.Path, "read_text", return_value=json.dumps({"memory": 512, "cpus": 1})):
            command = vm_manager.qemu_command("demo")
        self.assertIn("virtio-serial", command)
        self.assertIn("virtserialport,chardev=agent,name=agentos", command)
        self.assertIn("-enable-kvm", command)

    def test_guest_agent_executes_real_process(self):
        result = guest_agent.execute(["python3", "-c", "print(6 * 7)"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"].strip(), "42")


if __name__ == "__main__":
    unittest.main()
