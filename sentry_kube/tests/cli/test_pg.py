import base64
from io import StringIO
import pytest
from unittest import mock

from kubernetes.client import CoreV1Api
from kubernetes.client.models import V1Secret
from kubernetes.client.rest import ApiException

from sentry_kube.cli.pg.create_user import decode_userlist, merge_userlists, pg_scram_sha256, upload_plaintext_to_k8s_secret


@mock.patch('os.urandom', return_value=b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f')
def test_pg_scram_sha256(mock_urandom):
    result = pg_scram_sha256('helloworld')
    assert result == 'SCRAM-SHA-256$4096:AAECAwQFBgcICQoLDA0ODw==$fz0NTQhi0PzVISJdN1T1uSGlWXfLKg4O6uedGG0EfBQ=:hoPVYNrCgxf3TtXeuKoFHAtGQy4G+fMQvjNMs76hw+k='


class TestUserlist:
    @pytest.fixture(autouse=True)
    def setUp(self):
        self.userlist = '"user1" "SCRAM-SHA-256$111"\n"user2" "SCRAM-SHA-256$222"'
        self.userlist_b64 = base64.b64encode(self.userlist.encode('utf-8'))


    def test_decode_userlist(self):
        result = decode_userlist(self.userlist)
        assert result == {'user1': 'SCRAM-SHA-256$111', 'user2': 'SCRAM-SHA-256$222'}


    @mock.patch('sys.stdout', new_callable=StringIO)
    def test_merge_userlists_no_new_users(self, mock_stdout):
        result = merge_userlists(self.userlist_b64, {}, 'example')
        output = mock_stdout.getvalue()

        assert result is None
        assert 'example is up to date. No new users.' in output


    @mock.patch('sys.stdin', StringIO('yes'))
    @mock.patch('sys.stdout', new_callable=StringIO)
    def test_merge_userlists_add_new_user(self, mock_stdout):
        result = merge_userlists(self.userlist_b64, {"user3": {"scram": "SCRAM-SHA-256$333"}}, 'example')
        output = mock_stdout.getvalue()

        assert base64.b64decode(result) == b'"user1" "SCRAM-SHA-256$111"\n"user2" "SCRAM-SHA-256$222"\n"user3" "SCRAM-SHA-256$333"\n'
        assert 'example is up to date. No new users.' not in output


@mock.patch('sys.stdout', new_callable=StringIO)
def test_secret_does_not_exist(mock_stdout):
    core_api_mock = mock.create_autospec(CoreV1Api)
    core_api_mock.read_namespaced_secret.side_effect = ApiException(status=404)

    users = {}
    upload_plaintext_to_k8s_secret(core_api_mock, users, "example")
    output = mock_stdout.getvalue()

    assert core_api_mock.create_namespaced_secret.call_count == 1
    assert core_api_mock.create_namespaced_secret.call_args[1]["body"].metadata["name"] == "example"
    assert core_api_mock.create_namespaced_secret.call_args[1]["body"].data == {}
    assert "Kubernetes secret `default/example` is up to date. No new users." in output


@mock.patch('sys.stdin', StringIO('yes'))
@mock.patch('sys.stdout', new_callable=StringIO)
def test_secret_add_user(mock_stdout):
    secret_mock = mock.create_autospec(V1Secret)
    secret_mock.name = "example"
    secret_mock.data = {}

    core_api_mock = mock.create_autospec(CoreV1Api)
    core_api_mock.read_namespaced_secret.return_value = secret_mock

    users = {
        "alice": {
            "password": "letmein",
            "scram": "SCRAM-SHA-256$4096:salt$stored_key:server_key",
        }
    }

    upload_plaintext_to_k8s_secret(core_api_mock, users, "example")
    output = mock_stdout.getvalue()

    assert core_api_mock.patch_namespaced_secret.call_count == 1
    assert core_api_mock.patch_namespaced_secret.call_args[1]["name"] == "example"
    assert core_api_mock.patch_namespaced_secret.call_args[1]["body"]["data"] == {"alice": "bGV0bWVpbg=="}
    assert "Updated successfully." in output
