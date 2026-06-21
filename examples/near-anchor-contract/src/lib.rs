use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::collections::UnorderedMap;
use near_sdk::serde::{Deserialize, Serialize};
use near_sdk::{env, near, AccountId, BorshStorageKey, PanicOnDefault};

#[derive(BorshSerialize, BorshStorageKey)]
enum StorageKey {
    Anchors,
}

#[derive(BorshDeserialize, BorshSerialize, Clone, Serialize, Deserialize)]
#[serde(crate = "near_sdk::serde")]
pub struct AnchorRecord {
    pub ledger_root: String,
    pub event_count: u64,
    pub journal_sha256: String,
    pub receipt_sha256: Option<String>,
    pub artifact_uri: Option<String>,
    pub memo: Option<String>,
    pub korgex_version: Option<String>,
    pub predecessor: AccountId,
    pub block_timestamp_ms: u64,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Contract {
    anchors: UnorderedMap<String, AnchorRecord>,
}

#[near]
impl Contract {
    #[init]
    pub fn new() -> Self {
        Self {
            anchors: UnorderedMap::new(StorageKey::Anchors),
        }
    }

    /// Store a privacy-preserving Korgex receipt anchor.
    ///
    /// The contract stores hashes and metadata only. Raw prompts, code, tool
    /// arguments/results, and secrets should never be supplied here.
    pub fn anchor(
        &mut self,
        ledger_root: String,
        event_count: u64,
        journal_sha256: String,
        receipt_sha256: Option<String>,
        artifact_uri: Option<String>,
        memo: Option<String>,
        korgex_version: Option<String>,
    ) -> AnchorRecord {
        assert_hex_64("ledger_root", &ledger_root);
        assert_hex_64("journal_sha256", &journal_sha256);
        if let Some(hash) = receipt_sha256.as_ref() {
            assert_hex_64("receipt_sha256", hash);
        }
        assert!(event_count > 0, "event_count must be positive");

        let record = AnchorRecord {
            ledger_root: ledger_root.clone(),
            event_count,
            journal_sha256,
            receipt_sha256,
            artifact_uri,
            memo,
            korgex_version,
            predecessor: env::predecessor_account_id(),
            block_timestamp_ms: env::block_timestamp_ms(),
        };
        self.anchors.insert(&ledger_root, &record);
        env::log_str(&serde_json::json!({
            "standard": "korgex-anchor",
            "version": "1.0.0",
            "event": "anchor",
            "data": {
                "ledger_root": record.ledger_root,
                "event_count": record.event_count,
                "predecessor": record.predecessor,
                "block_timestamp_ms": record.block_timestamp_ms,
                "artifact_uri": record.artifact_uri,
                "memo": record.memo,
            }
        }).to_string());
        record
    }

    pub fn get_anchor(&self, ledger_root: String) -> Option<AnchorRecord> {
        assert_hex_64("ledger_root", &ledger_root);
        self.anchors.get(&ledger_root)
    }
}

fn assert_hex_64(name: &str, value: &str) {
    assert!(
        value.len() == 64 && value.as_bytes().iter().all(|b| b.is_ascii_hexdigit()),
        "{name} must be a 64-character hex string"
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::{accounts, VMContextBuilder};
    use near_sdk::{testing_env, NearToken};

    fn context() -> VMContextBuilder {
        let mut builder = VMContextBuilder::new();
        builder
            .predecessor_account_id(accounts(0))
            .attached_deposit(NearToken::from_yoctonear(0));
        builder
    }

    #[test]
    fn anchors_and_reads_back() {
        testing_env!(context().build());
        let mut contract = Contract::new();
        let root = "a".repeat(64);
        let journal = "b".repeat(64);
        let receipt = "c".repeat(64);
        let record = contract.anchor(
            root.clone(),
            7,
            journal.clone(),
            Some(receipt.clone()),
            Some("https://example.com/proof".to_string()),
            Some("fixed issue #123".to_string()),
            Some("0.36.0".to_string()),
        );
        assert_eq!(record.ledger_root, root);
        assert_eq!(record.event_count, 7);
        assert_eq!(record.predecessor, accounts(0));

        let saved = contract.get_anchor(root).expect("anchor saved");
        assert_eq!(saved.journal_sha256, journal);
        assert_eq!(saved.receipt_sha256, Some(receipt));
    }

    #[test]
    #[should_panic(expected = "ledger_root must be a 64-character hex string")]
    fn rejects_invalid_root() {
        testing_env!(context().build());
        let mut contract = Contract::new();
        contract.anchor("nope".to_string(), 1, "b".repeat(64), None, None, None, None);
    }
}
