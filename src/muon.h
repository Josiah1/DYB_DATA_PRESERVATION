#pragma once

const std::vector<std::string> kMuonFields = {
    "site", "detector", "triggerType", "triggerNumber", "triggerTimeSec", "triggerTimeNanoSec",
    "energy", "rawEvis", "x", "y", "z", "t", "dx", "dy", "dz", "dt",
    "energyStatus", "positionStatus", "directionStatus",
    "energyQuality", "positionQuality", "directionQuality"};

TTree* require_muon_tree(TFile& file, bool output = false) {
  const std::string path = "Event/Rec/ARMuon";
  auto* tree = dynamic_cast<TTree*>(file.Get(path.c_str()));
  if (!tree) throw std::runtime_error("muon mode requires tree: " + path);
  for (const auto& original : kMuonFields) {
    const auto name = output ? "arm_" + original : original;
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
                    Int_t& run, Long64_t& source_index, Long64_t& source_entry, MuonRow& row) {
  input->SetBranchStatus("*", false);
  if (!output) {
    rec_dir->cd();
    output = new TTree("ARMuon", "Muon reconstruction fields and source identity");
    output->Branch("runNumber", &run, "runNumber/I");
    output->Branch("sourceIndex", &source_index, "sourceIndex/L");
    output->Branch("sourceEntry", &source_entry, "sourceEntry/L");
    output->SetAutoFlush(0);
    output->SetAutoSave(0);
  }
  auto field = [&](const char* name, auto* address, const char* code) {
    const auto stored_name = std::string("arm_") + name;
    if (!output->GetBranch(stored_name.c_str()))
      output->Branch(stored_name.c_str(), address, (stored_name + "/" + code).c_str());
    if (std::string(input->GetLeaf(name)->GetTypeName()) !=
        output->GetLeaf(stored_name.c_str())->GetTypeName())
      throw std::runtime_error("unsupported muon branch type: " + std::string(name));
    bind(input, name, address);
  };
  field("site", &row.site, "S");
  field("detector", &row.detector, "S");
  field("triggerType", &row.triggerType, "i");
  field("triggerNumber", &row.triggerNumber, "i");
  field("triggerTimeSec", &row.triggerTimeSec, "I");
  field("triggerTimeNanoSec", &row.triggerTimeNanoSec, "I");
  field("energy", &row.energy, "F");
  field("x", &row.x, "F");
  field("y", &row.y, "F");
  field("z", &row.z, "F");
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
  output->SetBasketSize("*", kBasketBytes);
  for (source_entry = 0; source_entry < input->GetEntries(); ++source_entry) {
    if (input->GetEntry(source_entry) <= 0)
      throw std::runtime_error("cannot read muon entry");
    if (output->Fill() < 0)
      throw std::runtime_error("cannot copy muon entry in " + std::string(input->GetName()));
  }
  if (output->FlushBaskets() < 0) throw std::runtime_error("cannot flush muon output");
  input->ResetBranchAddresses();
}

void verify_muon(TFile& file, TTree* sources) {
  Long64_t count = 0, total = 0;
  Long64_t source_index = 0, source_entry = 0;
  Int_t run = 0;
  require_branches(sources, {"muonEntries"});
  if (sources->GetLeaf("muonEntries")->GetLenStatic() != 1 ||
      sources->GetLeaf("muonEntries")->GetLeafCount() ||
      std::string(sources->GetLeaf("muonEntries")->GetTypeName()) != "Long64_t")
    throw std::runtime_error("invalid muon source counts");
  bind(sources, "muonEntries", &count);
  auto* tree = require_muon_tree(file, true);
  require_branches(tree, {"runNumber", "sourceIndex", "sourceEntry"});
  bind(tree, "runNumber", &run);
  bind(tree, "sourceIndex", &source_index);
  bind(tree, "sourceEntry", &source_entry);
  for (Long64_t s = 0; s < sources->GetEntries(); ++s) {
    if (sources->GetEntry(s) <= 0) throw std::runtime_error("cannot read muon provenance");
    if (count < 0 || count > tree->GetEntries() - total)
      throw std::runtime_error("invalid muon source entry count");
    for (Long64_t e = 0; e < count; ++e) {
      if (tree->GetEntry(total + e) <= 0 || source_index != s || source_entry != e || run < 0)
        throw std::runtime_error("invalid muon entry provenance");
    }
    total += count;
  }
  if (total != tree->GetEntries())
    throw std::runtime_error("muon tree entry total differs from provenance");
  sources->ResetBranchAddresses();
  tree->ResetBranchAddresses();
}
