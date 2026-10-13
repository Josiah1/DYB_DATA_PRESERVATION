#pragma once

// Keep each reconstruction stream's own entry order and source identity.
const std::array<const char*, 3> kMuonTrees = {"AdSimple", "AdScaled", "ARMuon"};
const std::vector<std::string> kEventKeys = {
    "site", "detector", "triggerType", "triggerNumber", "triggerTimeSec", "triggerTimeNanoSec"};

std::vector<std::string> muon_fields(std::size_t index) {
  auto fields = kEventKeys;
  if (index == 0) {
    for (const char* name : {"energy", "x", "y", "z"}) fields.emplace_back(name);
  } else if (index == 2) {
    for (const char* name : {"energy", "rawEvis", "x", "y", "z", "t", "dx", "dy", "dz", "dt",
                             "energyStatus", "positionStatus", "directionStatus",
                             "energyQuality", "positionQuality", "directionQuality"})
      fields.emplace_back(name);
  }
  return fields;
}

TTree* require_muon_tree(TFile& file, std::size_t index) {
  const std::string path = std::string("Event/Rec/") + kMuonTrees[index];
  auto* tree = dynamic_cast<TTree*>(file.Get(path.c_str()));
  if (!tree) throw std::runtime_error("muon mode requires tree: " + path);
  for (const auto& name : muon_fields(index)) {
    auto* branch = tree->GetBranch(name.c_str());
    auto* leaf = tree->GetLeaf(name.c_str());
    if (!branch || !leaf || branch->GetListOfLeaves()->GetEntries() != 1 ||
        leaf->GetLeafCount() || leaf->GetLenStatic() != 1 ||
        std::string(leaf->GetTypeName()) == "Char_t")
      throw std::runtime_error("muon mode requires a scalar numeric branch: " + path + "/" + name);
  }
  return tree;
}

struct MuonRow {
  Short_t site = 0, detector = 0;
  UInt_t triggerType = 0, triggerNumber = 0;
  Int_t triggerTimeSec = 0, triggerTimeNanoSec = 0;
  Float_t energy = 0, rawEvis = 0, x = 0, y = 0, z = 0, t = 0;
  Float_t dx = 0, dy = 0, dz = 0, dt = 0;
  Int_t energyStatus = 0, positionStatus = 0, directionStatus = 0;
  Float_t energyQuality = 0, positionQuality = 0, directionQuality = 0;
};

void copy_muon_tree(TTree* input, TTree*& output, TDirectory* rec_dir,
                    std::size_t index, Int_t& run, Long64_t& source_index,
                    Long64_t& source_entry, TTree* base, MuonRow& row) {
  if (index < 2 && input->GetEntries() != base->GetEntries())
    throw std::runtime_error("AD reconstruction entry counts differ");
  input->SetBranchStatus("*", false);
  if (!output) {
    rec_dir->cd();
    output = new TTree(kMuonTrees[index], "Reconstruction fields and source identity");
    output->Branch("runNumber", &run, "runNumber/I");
    output->Branch("sourceIndex", &source_index, "sourceIndex/L");
    output->Branch("sourceEntry", &source_entry, "sourceEntry/L");
    output->SetAutoFlush(0);
    output->SetAutoSave(0);
  }
  auto field = [&](const char* name, auto* address, const char* code) {
    if (!output->GetBranch(name))
      output->Branch(name, address, (std::string(name) + "/" + code).c_str());
    if (std::string(input->GetLeaf(name)->GetTypeName()) != output->GetLeaf(name)->GetTypeName())
      throw std::runtime_error("unsupported muon branch type: " + std::string(name));
    bind(input, name, address);
  };
  field("site", &row.site, "S");
  field("detector", &row.detector, "S");
  field("triggerType", &row.triggerType, "i");
  field("triggerNumber", &row.triggerNumber, "i");
  field("triggerTimeSec", &row.triggerTimeSec, "I");
  field("triggerTimeNanoSec", &row.triggerTimeNanoSec, "I");
  if (index != 1) {
    field("energy", &row.energy, "F");
    field("x", &row.x, "F");
    field("y", &row.y, "F");
    field("z", &row.z, "F");
  }
  if (index == 2) {
    field("rawEvis", &row.rawEvis, "F");
    field("t", &row.t, "F");
    field("dx", &row.dx, "F");
    field("dy", &row.dy, "F");
    field("dz", &row.dz, "F");
    field("dt", &row.dt, "F");
    field("energyStatus", &row.energyStatus, "I");
    field("positionStatus", &row.positionStatus, "I");
    field("directionStatus", &row.directionStatus, "I");
    field("energyQuality", &row.energyQuality, "F");
    field("positionQuality", &row.positionQuality, "F");
    field("directionQuality", &row.directionQuality, "F");
  }
  output->SetBasketSize("*", kBasketBytes);
  for (source_entry = 0; source_entry < input->GetEntries(); ++source_entry) {
    if (input->GetEntry(source_entry) <= 0)
      throw std::runtime_error("cannot read muon entry");
    if (index < 2) {
      if (base->GetEntry(source_entry) <= 0) throw std::runtime_error("cannot read AD identity");
      for (const auto& key : kEventKeys)
        if (input->GetLeaf(key.c_str())->GetValueLong64() !=
            base->GetLeaf(key.c_str())->GetValueLong64())
          throw std::runtime_error("AD reconstruction event identities differ: " + key);
    }
    if (output->Fill() < 0)
      throw std::runtime_error("cannot copy muon entry in " + std::string(input->GetName()));
  }
  if (output->FlushBaskets() < 0) throw std::runtime_error("cannot flush muon output");
  input->ResetBranchAddresses();
}

void verify_muon(TFile& file, TTree* sources, Long64_t base_entries) {
  Long64_t counts[3] = {0}, totals[3] = {0};
  Long64_t source_index = 0, source_entry = 0;
  Int_t run = 0;
  require_branches(sources, {"muonEntries"});
  if (sources->GetLeaf("muonEntries")->GetLenStatic() != 3 ||
      sources->GetLeaf("muonEntries")->GetLeafCount() ||
      std::string(sources->GetLeaf("muonEntries")->GetTypeName()) != "Long64_t")
    throw std::runtime_error("invalid muon source counts");
  bind(sources, "muonEntries", counts);
  std::array<TTree*, 3> trees{};
  for (std::size_t i = 0; i < trees.size(); ++i) {
    trees[i] = require_muon_tree(file, i);
    require_branches(trees[i], {"runNumber", "sourceIndex", "sourceEntry"});
    bind(trees[i], "runNumber", &run);
    bind(trees[i], "sourceIndex", &source_index);
    bind(trees[i], "sourceEntry", &source_entry);
  }
  for (Long64_t s = 0; s < sources->GetEntries(); ++s) {
    if (sources->GetEntry(s) <= 0) throw std::runtime_error("cannot read muon provenance");
    for (std::size_t i = 0; i < trees.size(); ++i) {
      if (counts[i] < 0 || counts[i] > trees[i]->GetEntries() - totals[i])
        throw std::runtime_error("invalid muon source entry count");
      for (Long64_t e = 0; e < counts[i]; ++e) {
        if (trees[i]->GetEntry(totals[i] + e) <= 0 || source_index != s ||
            source_entry != e || run < 0)
          throw std::runtime_error("invalid muon entry provenance");
      }
      totals[i] += counts[i];
    }
  }
  for (std::size_t i = 0; i < trees.size(); ++i)
    if (totals[i] != trees[i]->GetEntries() || (i < 2 && totals[i] != base_entries))
      throw std::runtime_error("muon tree entry totals differ from provenance");
  sources->ResetBranchAddresses();
  for (auto* tree : trees) tree->ResetBranchAddresses();
}
