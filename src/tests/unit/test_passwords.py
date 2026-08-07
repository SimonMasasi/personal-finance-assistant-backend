from src.utils.passwords import PasswordManager


class TestHashing:
    def test_hash_verifies(self) -> None:
        digest = PasswordManager.hash_password("Test@1234")
        assert PasswordManager.verify_password("Test@1234", digest)

    def test_wrong_password_fails(self) -> None:
        digest = PasswordManager.hash_password("Test@1234")
        assert not PasswordManager.verify_password("Test@9999", digest)

    def test_hash_is_argon2id(self) -> None:
        assert PasswordManager.hash_password("Test@1234").startswith("$argon2id$")

    def test_hash_is_salted(self) -> None:
        """Two hashes of the same password must differ."""
        a = PasswordManager.hash_password("Test@1234")
        b = PasswordManager.hash_password("Test@1234")
        assert a != b

    def test_hash_does_not_contain_the_password(self) -> None:
        assert "Test@1234" not in PasswordManager.hash_password("Test@1234")

    def test_malformed_hash_returns_false_rather_than_raising(self) -> None:
        assert not PasswordManager.verify_password("Test@1234", "not-a-hash")

    def test_waste_time_never_raises(self) -> None:
        PasswordManager.waste_time()


class TestStrength:
    def test_accepts_a_strong_password(self) -> None:
        ok, _ = PasswordManager.is_strong_password("Test@1234")
        assert ok

    def test_rejects_short(self) -> None:
        ok, message = PasswordManager.is_strong_password("Ab1")
        assert not ok and "8 characters" in message

    def test_rejects_no_digit(self) -> None:
        ok, message = PasswordManager.is_strong_password("abcdefghij")
        assert not ok and "digit" in message

    def test_rejects_no_letter(self) -> None:
        ok, message = PasswordManager.is_strong_password("1234567890")
        assert not ok and "letter" in message
